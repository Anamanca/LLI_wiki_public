"use client";

import { cn } from "@/lib/utils";

interface HeaderProps {
  title: string;
  description?: string;
  className?: string;
}

export function Header({ title, description, className }: HeaderProps) {
  return (
    <div className={cn("mb-6", className)}>
      <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
      {description && (
        <p className="mt-1 text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
