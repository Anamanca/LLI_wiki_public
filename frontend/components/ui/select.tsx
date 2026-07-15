"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

interface SelectProps {
  value?: string;
  onValueChange: (value: string) => void;
  children: React.ReactNode;
  placeholder?: string;
  className?: string;
  displayValue?: string;
}

function Select({ value, onValueChange, children, placeholder, className, displayValue }: SelectProps) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          "flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
      >
        <SelectValue value={displayValue ?? value} placeholder={placeholder} />
        <ChevronDown className="h-4 w-4 opacity-50" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover text-popover-foreground shadow-md">
          <SelectContent onSelect={(v) => { onValueChange(v); setOpen(false); }}>
            {children}
          </SelectContent>
        </div>
      )}
    </div>
  );
}

function SelectValue({ value, placeholder }: { value?: string; placeholder?: string }) {
  return <span className={value ? "" : "text-muted-foreground"}>{value || placeholder || "Select..."}</span>;
}

function SelectContent({
  children,
  onSelect,
}: {
  children: React.ReactNode;
  onSelect?: (value: string) => void;
}) {
  return (
    <div className="py-1">
      {React.Children.map(children, (child) => {
        if (!React.isValidElement(child)) return null;
        if (!onSelect) return child;
        return React.cloneElement(child as React.ReactElement<{ onSelect: (v: string) => void }>, { onSelect });
      })}
    </div>
  );
}

interface SelectItemProps {
  value: string;
  children: React.ReactNode;
  onSelect?: (value: string) => void;
}

function SelectItem({ value, children, onSelect }: SelectItemProps) {
  return (
    <div
      className="relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground"
      onClick={() => onSelect?.(value)}
    >
      {children}
    </div>
  );
}

export { Select, SelectContent, SelectItem, SelectValue };
