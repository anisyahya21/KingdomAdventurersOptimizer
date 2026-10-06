import { Link } from "wouter";

export default function UpdatesPage() {
  return (
    <div className="min-h-screen bg-background transition-colors">
      <div className="max-w-4xl mx-auto px-4 py-10 space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">What is new?</h1>
        </div>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">06/05/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Fixed E/ Hat variants and B/ Legendary Shield variants so they now work correctly in Equipment pages, Exchange Calculator, and related lookups.</li>
            <li>You no longer need to type &quot;/&quot; in equipment searches. For example, searching &quot;A Kairo Sword&quot; now correctly finds &quot;A/ Kairo Sword&quot;.</li>
            <li>Improved zoom controls in Chaos Setup Lab with slider controls and smoother zoom behavior that keeps your map position stable.</li>
            <li>Fixed Chaos Setup Lab stone placement checks to prevent overlapping 2x2 stone footprints.</li>
            <li>Daily Rank now separates reward highlight timing and competition timing: rewards follow your local day, competition status follows JST.</li>
            <li>Populated Restaurant with live item data so it now works as a full searchable database.</li>
            <li>Added Orchard under Other Facilities in Shops.</li>
            <li>Updated Kairo Room reward text entries to match corrected loot data.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">02/05/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Performance improvements were made across the website, so data and guides should load faster now.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">01/05/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Fixed event timers so past events now show as inactive instead of showing a misleading countdown.</li>
            <li>Fixed E/ Hat (B)/(R) duplicate behavior when toggling the Head filter.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">30/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Updated Houses &amp; Plots display: slot labels are now clearer (Extra beds and Shelves), indoor slot wording is removed, and owner job info is now shown next to Houses &amp; Plots.</li>
            <li>Expanded Jobs Advanced Compare to include skill access and weapon access comparison.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">29/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Jobs now correctly follow game data for Battle-Type and Non Battle-Type.</li>
            <li>Added Advanced Compare to Jobs Database.</li>
            <li>Renamed skill categories to clearer labels: Attack, Attack magic, and Recovery magic.</li>
            <li>Monster database now supports checking stats at custom levels, not just fixed checkpoints.</li>
            <li>Fixed a bug where combat simulator duel summary could fail to appear.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">27/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Weekly Conquest now has a major map improvement with useful area tracking and better spawn visibility.</li>
            <li>Ground monster spawn data has been populated/corrected.</li>
            <li>World Map beta received major improvements, including fullscreen and screenshot flow upgrades.</li>
            <li>Monster images have now been added to Monster Spawns and Weekly Conquest, credits to <a href="https://kairosoft.fandom.com/wiki/Monsters_(Kingdom_Adventurers)" className="underline text-foreground" target="_blank" rel="noreferrer">kairosoft.fandom</a>.</li>
            <li>Guides now show a table of contents on mobile, and guide popups behave better on mobile now.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">26/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li><Link href="/jobs/Berserker" className="underline text-foreground">Berserker</Link> now not missing shield, Weapon classes &amp; Skill access information.</li>
            <li>Jobs pages now show Job ranges, ( searching Range, Deployment Range, Defog Range ). Check out <Link href="/jobs/Monarch" className="underline text-foreground">Monarch</Link>.</li>
            <li>B/ Legendary Shield (B)/(R) &amp; E/ Hat (B)/(R) are no longer missing slot or stat information, they now also show as independent items in Loadout-builder &amp; Equipment Builder.</li>
            <li>You can now add your own guide in the <a href="https://kingdom-adventures-community-tools.vercel.app/guides" className="underline text-foreground">Guides section</a>.</li>
            <li><Link href="/match-finder?tab=simulator" className="underline text-foreground">Marriage Simulator</Link> child rank results now use the data-backed rank formula for every parent-rank combination. Mixed-rank marriages now resolve correctly, and same-rank marriages now apply the proper same-rank child-rank bonus.</li>
            <li><Link href="/jobs" className="underline text-foreground">Jobs</Link> preset buttons are updated to C+1, B+2, A+3, S+4, and S+5 for comparing children from non-awakened parents.</li>
            <li>Added Set Awk to the <Link href="/jobs" className="underline text-foreground">Jobs Database</Link> bulk controls, letting you set all shown jobs to their max stat levels for a chosen awakening without changing their ranks.</li>
            <li>Fixed an off-by-one EXP range issue in the <Link href="/equipment-leveling-optimizer" className="underline text-foreground">Equipment Leveling Optimizer</Link>. Level cap calculations now stop at the correct target level instead of including one extra EXP row.</li>
            <li>Guide author tools have been improved. When you add your own guide, you can now manage guide links directly, including unlinking automatic links and adding your own custom links to selected words. You also receive a private edit link, so you can edit your guide later from another device.</li>
            <li>Guide links to <Link href="/match-finder?tab=simulator" className="underline text-foreground">Marriage Simulator</Link>, <Link href="/equipment-stats" className="underline text-foreground">Equipment</Link>, and <Link href="/jobs" className="underline text-foreground">Jobs</Link> now open as popups, so readers can check details without leaving the guide.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">25/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Added <Link href="/equipment-leveling-optimizer" className="underline text-foreground">Equipment Leveling Optimizer</Link>.</li>
            <li>Event timing offsets now work correctly.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">23/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Updated user interface for match-finder, and the individual jobs pages, so they no longer look stretched.</li>
            <li>The word MODIFIERS for Know-How Journal & Master Craftsman's Tools is now orange so people can't miss it in the facilities section.</li>
            <li>Master Craftsman's Tools now also applies to building items ( Sturdy Board, Large Nail, etc), and copper coins.</li>
            <li>The website now remembers your Know-How Journal & Master Craftsman's Tools modifiers for next session</li>
            <li>User interface improvements to Survey, groups now can be collapsed and expande, and are collapsed on default.</li>
            <li>Cash register Survey is no longer missing from Survey list</li>
            <li>Added the ability to offset timed events, Kairo Room, Job Center, and Wairo Dungeon follow your local time plus any offset you set. </li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">21/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>Going into a Jobs main page then returning to <Link href="/jobs" className="underline text-foreground">Job Database</Link> should no longer remove your filters.</li>
            <li>Pressing show all no longer removes your selected items in Equipment Stats.</li>
            <li>B/ Legendary Shield &amp; E/ Hat now show (B) (R) to help differentiate between them.</li>
            <li>Added <Link href="/survey" className="underline text-foreground">survey page</Link> &amp; survey Calc.</li>
            <li>Fixed mobile Equipment sort on phone so the default label shows Sort by instead of Name.</li>
            <li>Added Marriage Simulator parent-source controls so child stats can optionally follow Father or Mother jobs.</li>
            <li>Marriage simulator now supports two independent simulations., Added a bottom comparison summary between Simulation 1 and Simulation 2.</li>
            <li>Added Jobs preset buttons for C+1, B+2, A+3, S+4, and S+5. It sets all jobs to that rank, with that awakening and stats maxed to that awakening, a good way to compare children from non-awakened parents.</li>
            <li>Added copper coin upgrade cost to <Link href="/houses?tab=facilities" className="underline text-foreground">facilities</Link>.</li>
          </ol>
        </section>

        <section className="rounded-lg border bg-card p-5 shadow-sm space-y-3">
          <h2 className="text-lg font-bold">20/04/2026</h2>
          <ol className="list-decimal pl-5 space-y-2 text-sm text-muted-foreground">
            <li>
              Added <Link href="/equipment-stats#equipment-builder-tool" className="underline text-foreground">equipment builder slots</Link>, you can now rename, duplicate or create new loadout slots, and compare them easily
            </li>
            <li>Now max equipment shown on equipment stat page screen is 25 by default, 50, 100 and all are option, so reaching the equipment builder is not a pain.</li>
            <li>
              Now <Link href="/equipment-exchange" className="underline text-foreground">equipment exchange</Link> utilizes <Link href="/sync-devices" className="underline text-foreground">Device sync</Link> if you have devices synced.
            </li>
            <li>Any place that requires a lot of personalized inputs now have an export and import button, save the text to a note and load it anytime, ( this is only a fail safe ).</li>
            <li><Link href="/match-finder?tab=simulator" className="underline text-foreground">Marriage sim</Link> now correctly caps Max Level to 999.</li>
            <li>Imported the new improved map from KA GameData</li>
            <li>Weekly conquest layer in <Link href="/world-map" className="underline text-foreground">World Map</Link> is now more accurate and improved</li>
          </ol>
        </section>
      </div>
    </div>
  );
}
