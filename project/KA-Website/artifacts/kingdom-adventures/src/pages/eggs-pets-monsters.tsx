import { Link } from "wouter";
import { Egg, Skull } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const CARDS = [
  {
    href: "/eggs",
    title: "Eggs & Pets",
    description: "Kingdom Adventurers egg planner with I Want This Pet, I Have This Egg, pet outcomes, and feed item reference.",
    icon: Egg,
    badge: "Planner",
  },
  {
    href: "/monsters-pets",
    title: "Monsters & Pets",
    description: "Full monster and pet database with spawn locations, detailed stats, growth data, levels, and pet planning.",
    icon: Skull,
    badge: "Database",
  },
];

export default function EggsPetsMonstersPage() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      <div className="space-y-1">
        <h1 className="text-xl font-bold tracking-tight">Eggs, Pets & Monsters</h1>
        <div className="max-w-3xl space-y-2 text-sm leading-relaxed text-muted-foreground">
          <p>
            Plan Kingdom Adventurers eggs, pets, and monsters with tools that go beyond simple spawn lists.
            This section connects egg outcomes, feed items, monster locations, pet stats, growth data, and level-based details.
          </p>
          <p>
            Use the egg planner when you want a specific pet, or open the monster and pet database when you need detailed
            information for collecting, comparing, and building stronger teams.
          </p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {CARDS.map((card) => {
          const Icon = card.icon;
          return (
            <Link key={card.href} href={card.href}>
              <Card className="h-full shadow-sm hover:shadow-md hover:border-primary/30 transition-all cursor-pointer">
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
            </Link>
          );
        })}
      </div>
    </div>
  );
}

