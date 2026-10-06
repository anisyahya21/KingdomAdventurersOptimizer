import { Link } from "wouter";
import { ArrowUpDown, Coins, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const CARDS = [
  {
    href: "/equipment-stats",
    title: "Equipment Stats",
    description: "Browse and compare equipment stats at any level.",
    icon: ArrowUpDown,
    badge: "Stats",
  },
  {
    href: "/equipment-exchange",
    title: "Equipment Exchange",
    description: "Calculate the cheapest route into A-rank Kairo equipment using live trade prices and mined copper values.",
    icon: Coins,
    badge: "Calculator",
  },
  {
    href: "/equipment-leveling-optimizer",
    title: "Equipment Leveling Optimizer",
    description: "Calculate EXP gained from sacrificed equipment and prepare for cheapest leveling route optimization.",
    icon: TrendingUp,
    badge: "Optimizer",
  },
];

export default function EquipmentStatsExchangePage() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <h1 className="text-xl font-bold tracking-tight">Equipment Stats & Exchange</h1>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Choose between the Kingdom Adventurers equipment stats database, exchange calculator, and leveling optimizer.
          Use these tools to compare weapons, shields, armor, accessories, ranks, craft requirements, and exchange routes.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {CARDS.map((card) => {
          const Icon = card.icon;
          return (
            <Link key={card.href} href={card.href}>
              <Card className="h-full cursor-pointer shadow-sm transition-all hover:border-primary/30 hover:shadow-md">
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="rounded-lg bg-muted p-2">
                      <Icon className="h-5 w-5 text-primary" />
                    </div>
                    <Badge variant="outline">{card.badge}</Badge>
                  </div>
                  <CardTitle className="mt-2 text-base">{card.title}</CardTitle>
                </CardHeader>
                <CardContent>
                  <CardDescription className="text-xs leading-relaxed">{card.description}</CardDescription>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

