"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const ENTITY_TYPES = [
  { value: "stock_ticker", label: "Cổ phiếu" },
  { value: "commodity", label: "Hàng hóa" },
  { value: "person", label: "Người" },
  { value: "market_index", label: "Chỉ số" },
  { value: "macro_indicator", label: "Vĩ mô" },
  { value: "policy", label: "Chính sách" },
  { value: "location", label: "Khu vực" },
];

const PREDICATES = [
  { value: "is_subsidiary_of", label: "Sở hữu" },
  { value: "competes_with", label: "Cạnh tranh" },
  { value: "supplies_to", label: "Cung ứng" },
  { value: "invested_in", label: "Đầu tư" },
  { value: "is_CEO_of", label: "Lãnh đạo" },
  { value: "located_in", label: "Vị trí" },
  { value: "impacted_by", label: "Ảnh hưởng" },
];

interface KgFilterPanelProps {
  onApply: (filters: { entity_type?: string; predicate?: string; depth?: number }) => void;
  loading?: boolean;
}

export function KgFilterPanel({ onApply, loading }: KgFilterPanelProps) {
  const [selectedType, setSelectedType] = useState<string>("");
  const [selectedPredicate, setSelectedPredicate] = useState<string>("");
  const [depth, setDepth] = useState(1);

  return (
    <Card className="p-4 space-y-4">
      <h3 className="text-sm font-semibold">Bộ lọc</h3>

      <div>
        <p className="text-xs font-medium mb-1.5 text-muted-foreground">Loại thực thể</p>
        <div className="flex flex-wrap gap-1">
          {ENTITY_TYPES.map((et) => (
            <button
              key={et.value}
              onClick={() => setSelectedType(selectedType === et.value ? "" : et.value)}
              className={`px-2 py-1 rounded text-xs border transition-colors ${
                selectedType === et.value
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-background hover:bg-accent border-border"
              }`}
            >
              {et.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <p className="text-xs font-medium mb-1.5 text-muted-foreground">Quan hệ</p>
        <div className="flex flex-wrap gap-1">
          {PREDICATES.map((p) => (
            <button
              key={p.value}
              onClick={() => setSelectedPredicate(selectedPredicate === p.value ? "" : p.value)}
              className={`px-2 py-1 rounded text-xs border transition-colors ${
                selectedPredicate === p.value
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-background hover:bg-accent border-border"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <p className="text-xs font-medium mb-1.5 text-muted-foreground">Độ sâu: {depth}</p>
        <input
          type="range"
          min={1}
          max={3}
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
          className="w-full"
        />
      </div>

      <Button
        size="sm"
        className="w-full"
        disabled={loading}
        onClick={() =>
          onApply({
            entity_type: selectedType || undefined,
            predicate: selectedPredicate || undefined,
            depth,
          })
        }
      >
        {loading ? "Đang tải..." : "Áp dụng"}
      </Button>
    </Card>
  );
}