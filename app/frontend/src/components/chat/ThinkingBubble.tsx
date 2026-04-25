import { Bot } from "lucide-react";

export function ThinkingBubble() {
  return (
    <div className="flex gap-3">
      <div className="flex-shrink-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A855F7] flex items-center justify-center">
          <Bot className="w-4 h-4 text-white" />
        </div>
      </div>
      <div className="bg-[#18181B] border border-[#27272A] rounded-2xl rounded-tl-md px-4 py-3 flex items-center">
        <span className="thinking-shimmer text-sm font-medium select-none">
          Thinking...
        </span>
      </div>
    </div>
  );
}
