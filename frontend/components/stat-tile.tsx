import { Card, CardContent } from "@/components/ui/card";

/* Values wear text tokens, never series color — a headline number is not
   a chart mark. */
export function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: number | string;
  hint?: string;
}) {
  return (
    <Card>
      <CardContent className="pt-4">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          {label}
        </p>
        <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
        {hint && <p className="mt-0.5 text-xs text-secondary">{hint}</p>}
      </CardContent>
    </Card>
  );
}
