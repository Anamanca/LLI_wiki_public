import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString("vi-VN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trimEnd() + "...";
}

export function statusColor(status: string): string {
  switch (status) {
    case "done":
      return "bg-green-500";
    case "processing":
      return "bg-blue-500 animate-pulse";
    case "pending":
      return "bg-yellow-500";
    case "failed":
      return "bg-red-500";
    case "rate_limited":
      return "bg-orange-500";
    default:
      return "bg-gray-500";
  }
}

export function statusText(status: string): string {
  switch (status) {
    case "done":
      return "Hoàn thành";
    case "processing":
      return "Đang xử lý";
    case "pending":
      return "Chờ xử lý";
    case "failed":
      return "Thất bại";
    case "rate_limited":
      return "Rate Limited";
    default:
      return status;
  }
}
