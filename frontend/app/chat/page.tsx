import { Suspense } from "react";
import { ChatContent } from "./chat-content";

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatContent />
    </Suspense>
  );
}