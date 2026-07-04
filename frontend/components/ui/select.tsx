import * as React from "react";

import { cn } from "@/lib/utils";

export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      "flex h-9 rounded-md border border-line bg-surface px-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent",
      className,
    )}
    {...props}
  />
));
Select.displayName = "Select";
