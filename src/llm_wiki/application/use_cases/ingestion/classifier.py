"""Classify transcript using LLM - Vietnamese prompt with 3+1 retry strategy.

DEPRECATED: classifier.py is no longer used in the main wiki pipeline when
MERGE_CLASSIFY_ENABLED=true. Pass 1 (wiki_integrator._run_extraction_pass) now
handles classification with 100% transcript coverage via chunked extraction.

Still available as cold fallback when Pass 1 classification is empty.
Use env flag MERGE_CLASSIFY_ENABLED=false to force legacy flow.
Do not add new callers without discussing with the team.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM_PROMPT = """Bạn là chuyên gia phân tích dữ liệu và trích xuất \
kiến thức (Knowledge Extractor).
Nhiệm vụ của bạn là đọc bản dịch video và phân loại nội dung một cách khoa học, logic.

== HƯỚNG DẪN TRÍCH XUẤT ==
1. main_topic: Tiêu đề mô tả đầy đủ chủ đề cốt lõi (5-15 từ). Phải chứa thực thể chính.
2. domain: Chọn 1 giá trị chính xác: "finance", "stock_market", "macroeconomics",
   "real_estate", "crypto", "business", "technology", "general".
3. subtopics: Mảng các chủ đề phụ chi tiết. Hãy liệt kê cụ thể các sự kiện hoặc khái niệm
   được thảo luận.
4. key_entities: Liệt kê TẤT CẢ các thực thể: mã chứng khoán (VD: VCB, VNINDEX),
   tên công ty, tên ngườichỉ số kinh tế (CPI, GDP, lãi suất FED), các chính sách/luật pháp.
5. language: "en", "vi", hoặc "mixed".
6. summary_3sentences: Ba câu tóm tắt cực kỳ chi tiết, bao gồm: luận điểm chính,
   các con số/dữ liệu then chốt, và kết luận của diễn giả.
7. existing_pages_to_update: Mảng các slug bài viết hiện có cần được cập nhật
   (để trống nếu không biết).

== NGUYÊN TẮC QUAN TRỌNG (COMPENSATE FOR FLASH MODEL) ==
- Ưu tiên các dữ liệu định lượng (con số, tỷ lệ %).
- Không bỏ sót bất kỳ mã chứng khoán hoặc tên doanh nghiệp nào xuất hiện trong transcript.
- Viết tóm tắt có tính phân tích cao, không viết chung chung.

== VÍ DỤ OUTPUT MONG ĐỢI ==
VIDEO: "Phân tích thị trường chứng khoán 15/3: VN-Index giảm 12.5 điểm do khối ngoại
bán ròng 850 tỷ. Cổ phiếu ngân hàng VCB, BID giảm mạnh nhất. Chuyên gia Nguyễn Văn A
nhận định đây là nhịp điều chỉnh kỹ thuật, khuyến nghị mua vào vùng 1240-1250.
CPI tháng 3 dự báo tăng 0.5%."

OUTPUT:
{
  "main_topic": "VN-Index giảm 12.5 điểm do khối ngoại bán ròng, chuyên gia khuyến nghị"
                " mua vào vùng hỗ trợ",
  "domain": "stock_market",
  "subtopics": [
    "Áp lực bán ròng khối ngoại",
    "Diễn biến cổ phiếu ngân hàng VCB và BID",
    "Nhận định và khuyến nghị của chuyên gia",
    "Dự báo CPI tháng 3"
  ],
  "key_entities": [
    "VN-Index", "VCB", "BID", "Nguyễn Văn A", "CPI",
    "12.5 điểm", "850 tỷ", "1240-1250 điểm"
  ],
  "language": "vi",
  "summary_3sentences": "VN-Index giảm 12.5 điểm trong phiên 15/3 do áp lực bán ròng 850 tỷ"
                        " đồng từ khối ngoại, tập trung vào nhóm ngân hàng VCB và BID. Chuyên gia"
                        " Nguyễn Văn A nhận định đây chỉ là nhịp điều chỉnh kỹ thuật và khuyến"
                        " nghị nhà đầu tư mua vào khi chỉ số về vùng 1240-1250 điểm. CPI tháng 3"
                        " được dự báo tăng 0.5%, không gây áp lực lớn lên mặt bằng lãi suất.",
  "existing_pages_to_update": []
}

Output DUY NHẤT một JSON object."""


RateLimitError = type("RateLimitError", (Exception,), {})


def _extract_json_from_llm_response(content: str) -> dict:
    """Extract JSON object from LLM response, handling markdown fences."""
    # Try to find JSON inside markdown code fences first
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if fence_match:
        content = fence_match.group(1).strip()

    # Try to find a JSON object with balanced braces
    start = content.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    depth = 0
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start : i + 1])
    raise ValueError("Unterminated JSON object in LLM response")


def _build_classify_messages(transcript_text: str) -> list[dict[str, str]]:
    """Build system + user messages for classification."""
    # Truncate transcript if too long (reserve for LLM context limit ~100K tokens / 400K chars)
    max_chars = 400_000
    text = transcript_text[:max_chars]
    if len(transcript_text) > max_chars:
        text += "\n\n[Transcript truncated due to length — processed first chunk]"

    return [
        {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": f"Transcript:\n\n{text}"},
    ]


async def classify_transcript(
    transcript,
    llm_client,
    timeout: float = 180.0,
) -> dict:
    """Classify a video transcript using LLM.

    Strategy:
      Attempt 1-3: primary model, backoff 1s/4s/16s
      Attempt 4: fallback model
      All fail: raise RuntimeError

    Args:
        transcript: Dict with 'raw_text', 'video_id', 'segments' keys
        llm_client: Object with chat_completion_raw(messages, model, temperature) method
                    that returns dict like {"choices": [{"message": {"content": "..."}}]}
        timeout: Total timeout per API call (seconds)

    Returns:
        dict with classification result
    """
    video_id = (
        transcript.get("video_id", "unknown")
        if isinstance(transcript, dict)
        else getattr(transcript, "video_id", "unknown")
    )
    raw_text = (
        transcript.get("raw_text", "")
        if isinstance(transcript, dict)
        else getattr(transcript, "raw_text", "")
    )
    segments = (
        transcript.get("segments", [])
        if isinstance(transcript, dict)
        else getattr(transcript, "segments", [])
    )

    if not raw_text and not segments:
        logger.warning("Empty transcript for %s — cannot classify", video_id)
        return {
            "main_topic": "Unknown (no captions)",
            "language": "unknown",
            "domain": "general",
            "subtopics": [],
            "key_entities": [],
            "summary_3sentences": "",
            "existing_pages_to_update": [],
        }

    text_for_llm = raw_text or " ".join(
        s.get("text", "") if isinstance(s, dict) else s.text for s in segments
    )
    messages = _build_classify_messages(text_for_llm)

    # Use default models. Caller can override by passing LLM client with custom model.
    from llm_wiki.config import settings

    primary_model = settings.opencode_primary_model
    fallback_model = settings.opencode_fallback_model

    models_to_try: list[tuple[str, float]] = [
        (primary_model, 1.0),
        (primary_model, 4.0),
        (primary_model, 16.0),
    ]

    for model, backoff in models_to_try:
        try:
            resp = await asyncio.wait_for(
                llm_client.chat_completion_raw(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=4096,
                ),
                timeout=timeout,
            )
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            # DeepSeek reasoning models may consume all tokens on reasoning_content,
            # leaving content empty. Fall back to reasoning_content if available.
            if not content.strip():
                reasoning = (
                    resp.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
                )
                if reasoning.strip():
                    logger.warning(
                        "Classifier content empty — falling back to reasoning_content (%d chars)",
                        len(reasoning),
                    )
                    content = reasoning
            if not content.strip():
                raise ValueError("Empty response from LLM")

            data = _extract_json_from_llm_response(content)
            logger.info(
                "Classification OK for %s (model=%s, topic=%s)",
                video_id,
                model,
                data.get("main_topic", ""),
            )
            return data

        except TimeoutError:
            logger.warning(
                "Classification timed out for %s on %s (attempt, backoff=%.0fs)",
                video_id,
                model,
                backoff,
            )
        except RateLimitError:
            logger.warning("Rate limited on %s — propagating", model)
            raise
        except Exception as exc:
            logger.warning(
                "Classification attempt failed (model=%s, backoff=%.0fs): %s",
                model,
                backoff,
                exc,
            )
            await asyncio.sleep(max(backoff, 1))

    # Fallback: try fallback model
    logger.warning(
        "Primary model exhausted for %s — falling back to %s",
        video_id,
        fallback_model,
    )
    try:
        resp = await asyncio.wait_for(
            llm_client.chat_completion_raw(
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            ),
            timeout=timeout,
        )
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content.strip():
            reasoning = resp.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
            if reasoning.strip():
                logger.warning(
                    "Classifier fallback content empty — falling back to reasoning_content"
                )
                content = reasoning
        data = _extract_json_from_llm_response(content)
        logger.info("Classification OK with fallback model for %s", video_id)
        return data
    except Exception as exc:
        logger.error(
            "Both primary and fallback classification failed for %s: %s",
            video_id,
            exc,
        )
        raise RuntimeError(f"Classification failed for {video_id}: all attempts exhausted") from exc
