import { useMemo } from "react";
import { ArrowRight, BriefcaseBusiness, Heart } from "lucide-react";
import { Link } from "wouter";
import { localSharedData } from "@/lib/local-shared-data";

type SharedJob = {
  category?: string;
  type?: "combat" | "non-combat";
};

type SharedData = {
  jobs?: Record<string, SharedJob>;
};

function getGroupFromCategory(job: SharedJob): "battle" | "trader" | "worker" | null {
  const category = job.category?.trim().toLowerCase();
  if (category === "fighter" || category === "1") return "battle";
  if (category === "trader" || category === "2") return "trader";
  if (category === "worker" || category === "0") return "worker";
  if (job.type === "combat") return "battle";
  return null;
}

export default function JobsMarriagePage() {
  const data = localSharedData as SharedData;

  const groupedJobs = useMemo(() => {
    const grouped: Record<"battle" | "trader" | "worker", string[]> = {
      battle: [],
      trader: [],
      worker: [],
    };

    Object.entries(data?.jobs ?? {}).forEach(([name, job]) => {
      const group = getGroupFromCategory(job ?? {});
      if (!group) return;
      grouped[group].push(name);
    });

    grouped.battle.sort((a, b) => a.localeCompare(b));
    grouped.trader.sort((a, b) => a.localeCompare(b));
    grouped.worker.sort((a, b) => a.localeCompare(b));
    return grouped;
  }, [data]);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="rounded-sm border border-border bg-card px-4 py-3 shadow-sm space-y-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Tools</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-1 gap-2">
            <Link href="/jobs" className="block">
              <article className="rounded-sm border border-border bg-background px-3 py-2 transition-colors hover:bg-muted/40">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <BriefcaseBusiness className="w-4 h-4 text-sky-500" />
                    <h3 className="text-sm font-semibold text-foreground">Job Database</h3>
                  </div>
                  <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Explore jobs with stats, battle type, rank behavior, equipment slot access, and skill accessibility.
                </p>
              </article>
            </Link>

            <Link href="/match-finder" className="block">
              <article className="rounded-sm border border-border bg-background px-3 py-2 transition-colors hover:bg-muted/40">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Heart className="w-4 h-4 text-rose-600" />
                    <h3 className="text-sm font-semibold text-foreground">Match Finder & Marriage Sim</h3>
                  </div>
                  <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  Use Match Finder, Marriage Simulator, and pair data tools to plan children, affinity, and awakening outcomes.
                </p>
              </article>
            </Link>
          </div>
        </div>

        <nav className="rounded-sm border border-border bg-card px-4 py-3 shadow-sm" aria-label="Table of contents">
          <h2 className="text-lg font-bold uppercase tracking-wide text-foreground">Contents</h2>
          <ol className="mt-2 space-y-1 text-sm text-foreground">
            <li><a href="#jobs-types" className="hover:underline">Jobs and Profession Types</a></li>
            <li><a href="#skills-slots" className="hover:underline">Skills and Skill Slots</a></li>
            <li><a href="#acquiring-allies" className="hover:underline">Acquiring Allies</a></li>
            <li><a href="#ranks" className="hover:underline">Ranks</a></li>
            <li><a href="#level-cap-growth" className="hover:underline">Level Cap and Growth</a></li>
            <li><a href="#awakening" className="hover:underline">Awakening</a></li>
          </ol>
        </nav>
      </section>

      <article className="rounded-sm border border-border bg-card px-4 py-1 shadow-sm">
        <section id="jobs-types" className="py-3 scroll-mt-24">
          <h2 className="text-2xl font-bold text-foreground">Jobs and Profession Types</h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>Jobs/professions are the residents/allies of your town.</p>
            <p>There are three types of jobs.</p>
            <p>
              <span className="text-base font-bold text-foreground">Battle-Type ({groupedJobs.battle.length}):</span>{" "}
              {groupedJobs.battle.length > 0
                ? groupedJobs.battle.map((name, index) => (
                    <span key={`battle-${name}`}>
                      <Link href={`/jobs/${encodeURIComponent(name)}`} className="underline underline-offset-2 hover:opacity-80 transition-opacity">
                        {name}
                      </Link>
                      {index < groupedJobs.battle.length - 1 ? ", " : ""}
                    </span>
                  ))
                : "None"}
            </p>
            <p>
              <span className="text-base font-bold text-foreground">Traders ({groupedJobs.trader.length}):</span>{" "}
              {groupedJobs.trader.length > 0
                ? groupedJobs.trader.map((name, index) => (
                    <span key={`trader-${name}`}>
                      <Link href={`/jobs/${encodeURIComponent(name)}`} className="underline underline-offset-2 hover:opacity-80 transition-opacity">
                        {name}
                      </Link>
                      {index < groupedJobs.trader.length - 1 ? ", " : ""}
                    </span>
                  ))
                : "None"}
              . Traders can open shops when given a <Link href="/houses" className="underline underline-offset-2 hover:opacity-80 transition-opacity">plot</Link>.
            </p>
            <p>
              <span className="text-base font-bold text-foreground">Workers ({groupedJobs.worker.length}):</span>{" "}
              {groupedJobs.worker.length > 0
                ? groupedJobs.worker.map((name, index) => (
                    <span key={`worker-${name}`}>
                      <Link href={`/jobs/${encodeURIComponent(name)}`} className="underline underline-offset-2 hover:opacity-80 transition-opacity">
                        {name}
                      </Link>
                      {index < groupedJobs.worker.length - 1 ? ", " : ""}
                    </span>
                  ))
                : "None"}
            </p>
          </div>
        </section>

        <section id="skills-slots" className="border-t border-border py-3 scroll-mt-24">
          <h2 className="text-xl font-bold text-foreground">Skills and Skill Slots</h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>Each ally comes with <Link href="/skills" className="underline underline-offset-2 hover:opacity-80 transition-opacity">skills</Link> based on the ally. These can be removed or changed freely.</p>
            <p>The number of skill slots depends on rank.</p>
            <p>Using the Skill Slot Up item grants the ally more skill slots.</p>
            <p>Children inherit skill slots from their parents.</p>
            <p>The maximum number of skills an ally can have is nine.</p>
          </div>
        </section>

        <section id="acquiring-allies" className="border-t border-border py-3 scroll-mt-24">
          <h2 className="text-xl font-bold text-foreground">Acquiring Allies</h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>Non-marriage allies can be acquired by rolling gacha using Job Trade Tickets or Diamonds.</p>
            <p>They can also be bought with Diamonds from <Link href="/job-center" className="underline underline-offset-2 hover:opacity-80 transition-opacity">Job Center Facility</Link>, some are acquired by unlocking the map, and finally you can breed by marrying first-generation allies.</p>
            <p>Marriage-exclusive jobs can only be acquired from marrying two first-generation allies.</p>
            <p><Link href="/jobs/Scholar" className="underline underline-offset-2 hover:opacity-80 transition-opacity">Scholar</Link> can be acquired through survey or from treasure crates.</p>
            <p><Link href="/jobs/Monarch" className="underline underline-offset-2 hover:opacity-80 transition-opacity">Monarch</Link> is a unique job, and you can only have one <Link href="/jobs/Monarch" className="underline underline-offset-2 hover:opacity-80 transition-opacity">Monarch</Link>. Monarch rank increases as you level up your <Link href="/town-rank" className="underline underline-offset-2 hover:opacity-80 transition-opacity">Town Hall</Link>.</p>
          </div>
        </section>

        <section id="ranks" className="border-t border-border py-3 scroll-mt-24">
          <h2 className="text-xl font-bold text-foreground">Ranks</h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>There are 6 ranks a job can have: S, A, B, C, D, and F.</p>
            <p>Stat growth per level is different for every rank.</p>
            <p>F rank is unique to Scholar.</p>
          </div>
        </section>

        <section id="level-cap-growth" className="border-t border-border py-3 scroll-mt-24">
          <h2 className="text-xl font-bold text-foreground">Level Cap and Growth</h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>Allies have a level cap that is the same for all ranks.</p>
            <p>Different jobs have different max levels in every stat.</p>
            <p>Different jobs and different ranks have different growth values per level in every stat.</p>
            <p>Max level cap can be increased by awakening allies.</p>
          </div>
        </section>

        <section id="awakening" className="border-t border-border py-3 scroll-mt-24">
          <h2 className="text-xl font-bold text-foreground">Awakening</h2>
          <div className="mt-2 space-y-2 text-sm leading-relaxed text-muted-foreground">
            <p>Every 100 points of awakening grants the ally +30 max level in all stats.</p>
            <p>The max level of all stats is 999.</p>
            <p>Allies can be awakened by feeding them the same kind of job of any rank.</p>
            <p>You can only feed allies that have not yet been assigned to a town.</p>
            <p>Allies can also be awakened by feeding them scholars.</p>
          </div>
        </section>
      </article>
    </div>
  );
}
