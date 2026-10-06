import { Link } from "wouter";
import { Calculator, Database, MapPin } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const CARDS = [
  {
    href: "/monster-spawns",
    title: "Monster Spawns & Loot",
    description:
      "One card per monster: spawn biome and level, the cave that spawns it, the treasure box it drops with every content and chance, and average XP per kill.",
    icon: MapPin,
    badge: "Live",
  },
  {
    href: "/monster-pet-stats",
    title: "Monster & Pet Stats",
    description: "Detailed Kingdom Adventurers monster and pet stats database with base levels, growth, and level-based stat tables.",
    icon: Database,
    badge: "Stats",
  },
  {
    href: "/monster-xp",
    title: "Monster XP per Kill",
    description: "Compare Ground/dirt and biome XP per kill by area level, including stat-by-stat XP and XP Up skills.",
    icon: Calculator,
    badge: "Calculator",
  },
];

export default function MonstersPetsPage() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <h1 className="text-xl font-bold tracking-tight">Monsters & Pets</h1>
        <div className="max-w-3xl space-y-2 text-sm leading-relaxed text-muted-foreground">
          <p>
            Browse the Kingdom Adventurers monster database and pet database in detail, including spawn locations,
            monster levels, pet base stats, growth, and level-based stat comparisons.
          </p>
          <p>
            Use Monster Spawns when you need to find where an enemy appears, or Monster & Pet Stats when you want
            the deeper data for collecting, pet planning, and team comparisons.
          </p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {CARDS.map((card) => {
          const Icon = card.icon;
          const content = (
            <Card className="h-full shadow-sm transition-all hover:shadow-md hover:border-primary/30 cursor-pointer">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="p-2 rounded-lg bg-muted">
                    <Icon className="w-5 h-5 text-primary" />
                  </div>
                  <Badge variant="outline">{card.badge}</Badge>
                </div>
                <CardTitle className="text-base mt-2">{card.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <CardDescription className="text-xs leading-relaxed">{card.description}</CardDescription>
              </CardContent>
            </Card>
          );

          return <Link key={card.href} href={card.href}>{content}</Link>;
        })}
      </div>
    </div>
  );
}

