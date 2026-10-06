import { isSkillCraftable } from "@/lib/skill-crafting";
import { useState, useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import {
  Loader2, Pencil, Check, X,
  BookOpen, Search, Zap, Info,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { PageHeader } from "@/components/ka/page-header";
import { ToneBadge } from "@/components/ka/badges";
import { fetchSharedWithFallback } from "@/lib/local-shared-data";
import { apiUrl } from "@/lib/api";
import { getSkillIcon } from "@/lib/skill-icons";


type Skill = {
  name: string;
  studioLevel?: number;
  craftingIntelligence?: number;
  buyPrice?: number;
  sellPrice?: number;
  description?: string;
  weaponResistance?: string;
  flags?: number;
};


function getSkillTypeLabel(skill: Skill) {
  if (typeof skill.flags === "number") {
    if (skill.flags & 512) return "Attack";
    if (skill.flags & 1024) return "Attack Magic";
    if (skill.flags & 2048) return "Recovery magic";
  }
  return getSkillTypeLabelByName(skill.name);
}

type SharedData = { skills?: Record<string, Skill> };

function getSkillTypeLabelByName(skillName: string) {
  const normalized = skillName.trim();
  const exact = SKILL_TYPE_BY_NAME.get(normalized);
  if (exact) return exact;

  const patterns: Array<[RegExp, "Attack" | "Attack Magic" | "Recovery magic"]> = [
    [/^\d+-Hit Attack$/i, "Attack"],
    [/^Area Attack\s+.+$/i, "Attack"],
    [/^Armor Breaker\s+.+$/i, "Attack"],
    [/^Direct Attack\s+.+$/i, "Attack"],
    [/^Sandman\s+.+$/i, "Attack"],
    [/^Fire Magic\s+.+$/i, "Attack Magic"],
    [/^Ice Magic\s+.+$/i, "Attack Magic"],
    [/^Lightning Magic\s+.+$/i, "Attack Magic"],
    [/^Heal\s+.+$/i, "Recovery magic"],
    [/^Revive\s+\d+%$/i, "Recovery magic"],
  ];

  for (const [pattern, type] of patterns) {
    if (pattern.test(normalized)) return type;
  }

  return undefined;
}

const SKILL_TYPE_BY_NAME = new Map<string, "Attack" | "Attack Magic" | "Recovery magic">([
  ["<0>-Hit Attack", "Attack"],
  ["Area Attack <0>", "Attack"],
  ["Armor Breaker <0>", "Attack"],
  ["Arrow Rain", "Attack"],
  ["Counter", "Attack"],
  ["Critical UP", "Attack"],
  ["Critical UP+", "Attack"],
  ["Direct Attack <0>", "Attack"],
  ["Dodge UP", "Attack"],
  ["Full Reflect", "Attack"],
  ["Half Reflect", "Attack"],
  ["Myriad Arrows", "Attack"],
  ["Parry", "Attack"],
  ["Perfect Dodge", "Attack"],
  ["Sandman <0>", "Attack"],
  ["Fire Magic <0>", "Attack Magic"],
  ["Ice Magic <0>", "Attack Magic"],
  ["Lightning Magic <0>", "Attack Magic"],
  ["Heal <0>", "Recovery magic"],
  ["Revive <0>%", "Recovery magic"],
]);

const EXCLUDED_SKILL_NAMES = new Set(["normal attack", "gun attack", "critical hit"]);
function useSharedData() {
  return useQuery({
    queryKey: ["ka-shared"],
    queryFn: () => fetchSharedWithFallback<SharedData>(apiUrl("/shared")),
    staleTime: 5 * 60_000,
  });
}

async function persistSkills(skills: Record<string, Skill>, userName: string, desc: string) {
  await fetch(apiUrl("/skills"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      data: skills,
      history: { userName: userName || "anonymous", changeType: "skill", itemName: "skills", description: desc },
    }),
  });
}


function useUserName() {
  const [name, setName] = useState(() => localStorage.getItem("ka_username") ?? "");
  const save = (n: string) => { setName(n); localStorage.setItem("ka_username", n); };
  return { name, save };
}

function NamePrompt({ onSave, onCancel }: { onSave: (n: string) => void; onCancel: () => void }) {
  const [val, setVal] = useState("");
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="max-w-xs">
        <DialogHeader><DialogTitle>Enter your name</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">So the community knows who made changes.</p>
        <Input value={val} onChange={(e) => setVal(e.target.value)} placeholder="Your name…" className="h-8 text-sm"
          onKeyDown={(e) => { if (e.key === "Enter" && val.trim()) onSave(val.trim()); }} />
        <Button onClick={() => { if (val.trim()) onSave(val.trim()); }} className="h-8">Save</Button>
      </DialogContent>
    </Dialog>
  );
}

function numCell(val: number | null | undefined, onChange: (v: number | undefined) => void, editing: boolean, prefix?: string) {
  if (!editing) {
    return val != null
      ? <span className="tabular-nums">{prefix}{val.toLocaleString()}</span>
      : <span className="text-muted-foreground/40">—</span>;
  }
  return (
    <Input
      type="number"
      min={0}
      value={val ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
      className="h-7 text-sm px-2 w-full"
      placeholder="—"
    />
  );
}

export default function SkillsPage() {
  const { name: userName, save: saveUserName } = useUserName();
  const [promptName, setPromptName] = useState(false);
  const [pendingFn, setPendingFn] = useState<(() => void) | null>(null);
  const [pageNote, setPageNote] = useState(() => localStorage.getItem("ka_note_skills") ?? "");
  const [showNote, setShowNote] = useState(false);
  const qc = useQueryClient();
  const { data, isLoading } = useSharedData();
  const [search, setSearch] = useState(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("search") ?? "";
  });
  const [sortKey, setSortKey] = useState<"name" | "studioLevel" | "craftingIntelligence" | "buyPrice" | "sellPrice" | "type" | "craftable">("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const skills: Record<string, Skill> = data?.skills ?? {};

  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortDir((d) => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  };
  const sorted = Object.values(skills)
    .filter((skill) => !EXCLUDED_SKILL_NAMES.has(skill.name.trim().toLowerCase()))
    .sort((a, b) => {
      const dir = sortDir === "asc" ? 1 : -1;
      if (sortKey === "name") return dir * a.name.localeCompare(b.name);
      if (sortKey === "type") {
        const aType = getSkillTypeLabel(a) ?? "";
        const bType = getSkillTypeLabel(b) ?? "";
        return dir * aType.localeCompare(bType);
      }
      if (sortKey === "craftable") {
        const aCraft = isSkillCraftable(a) ? 1 : 0;
        const bCraft = isSkillCraftable(b) ? 1 : 0;
        return dir * (aCraft - bCraft);
      }
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      return dir * ((av as number) < (bv as number) ? -1 : (av as number) > (bv as number) ? 1 : 0);
    });
  const filtered = search.trim()
    ? sorted.filter((s) => s.name.toLowerCase().includes(search.toLowerCase()))
    : sorted;

  const withName = useCallback((fn: () => void) => {
    if (!userName) { setPendingFn(() => fn); setPromptName(true); }
    else fn();
  }, [userName]);

  const onNameSaved = (n: string) => {
    saveUserName(n); setPromptName(false);
    if (pendingFn) { pendingFn(); setPendingFn(null); }
  };
  const onNamePromptCancelled = () => {
    setPromptName(false);
    setPendingFn(null);
  };

  const saveSkills = useCallback((updated: Record<string, Skill>, desc: string) => {
    withName(() => {
      const effectiveUserName = userName || localStorage.getItem("ka_username") || "";
      qc.setQueryData(["ka-shared"], (old: SharedData | undefined) => old ? { ...old, skills: updated } : old);
      persistSkills(updated, effectiveUserName, desc).then(() => qc.invalidateQueries({ queryKey: ["ka-shared"] }));
    });
  }, [qc, userName, withName]);

  // ── Adding a new skill ──
  // ── Inline editing ──
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Skill | null>(null);

  const startEdit = (skill: Skill) => { setEditingName(skill.name); setEditDraft({ ...skill }); };
  const cancelEdit = () => { setEditingName(null); setEditDraft(null); };
  const commitEdit = () => {
    if (!editDraft || !editingName) return;
    const originalDescription = skills[editingName]?.description ?? "";
    const draftDescription = editDraft.description ?? "";
    if (originalDescription === draftDescription) {
      cancelEdit();
      return;
    }
    const updated = { ...skills };
    updated[editingName] = { ...skills[editingName], description: editDraft.description };
    saveSkills(updated, `Updated skill notes: ${editingName}`);
    setEditingName(null);
    setEditDraft(null);
  };

  return (
    <div className="min-h-screen bg-background transition-colors">
      {promptName && <NamePrompt onSave={onNameSaved} onCancel={onNamePromptCancelled} />}

      <div className="max-w-5xl mx-auto px-4 py-6">
        <PageHeader
          icon={<BookOpen className="w-5 h-5 text-emerald-500" />}
          title="Skills Database"
          className="mb-4"
          actions={(
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="icon" onClick={() => setShowNote((v) => !v)} className="h-8 w-8 text-muted-foreground" title="Personal notes (private, stored on this device)">
                <Info className="w-3.5 h-3.5" />
              </Button>
            </div>
          )}
        />

        {showNote && (
          <div className="mb-4">
            <textarea
              value={pageNote}
              onChange={(e) => setPageNote(e.target.value)}
              onBlur={() => localStorage.setItem("ka_note_skills", pageNote)}
              placeholder="Personal notes for this page… (only visible to you, saved on this device)"
              className="w-full h-20 text-sm rounded-md border border-input bg-muted/20 px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground/40"
            />
          </div>
        )}
        {/* Toolbar */}
        <div className="flex gap-2 mb-4 flex-wrap">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search skills…" className="h-8 text-sm pl-8" />
          </div>
        </div>

        <Card className="shadow-sm">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Skills</CardTitle>
            <CardDescription className="text-xs">
              Sheet-derived skill data is read-only here. Only the Tips / Notes text is editable for community context.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/40">
                      {([
                        { key: "name", label: "Skill Name", align: "left", cls: "w-[200px]" },
                        { key: "studioLevel", label: "Studio Level", align: "center", cls: "w-[100px]" },
                        { key: "craftingIntelligence", label: null, align: "center", cls: "w-[130px]" },
                        { key: "buyPrice", label: "Buy Price", align: "center", cls: "w-[100px]" },
                        { key: "sellPrice", label: "Sell Price", align: "center", cls: "w-[100px]" },
                        { key: "type", label: "Type", align: "center", cls: "w-[110px]" },
                        { key: "craftable", label: "Craftable", align: "center", cls: "w-[100px]" },
                      ] as const).map(({ key, label, align, cls }) => (
                        <th key={key} className={`px-3 py-2 text-xs font-medium text-muted-foreground select-none cursor-pointer hover:text-foreground transition-colors ${cls} text-${align}`}
                          onClick={() => toggleSort(key as typeof sortKey)}>
                          <span className="inline-flex items-center gap-1">
                            {key === "craftingIntelligence"
                              ? <><Zap className="w-3 h-3 text-yellow-500" />Crafting Intel</>
                              : label}
                            {sortKey === key
                              ? (sortDir === "asc" ? " ↑" : " ↓")
                              : <span className="text-muted-foreground/30"> ↕</span>}
                          </span>
                        </th>
                      ))}
                      <th className="text-left px-3 py-2 text-xs font-medium text-muted-foreground">Tips / Notes</th>
                      <th className="w-16" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">

                    {filtered.length === 0 && (
                      <tr><td colSpan={9} className="text-center py-12 text-sm text-muted-foreground">
                        {search ? "No skills match your search." : "No skills found."}
                      </td></tr>
                    )}

                    {filtered.map((skill) => {
                      const isEditing = editingName === skill.name;
                      const d = isEditing ? editDraft! : skill;
                      const skillIcon = getSkillIcon(skill.name);
                      const craftable = isSkillCraftable(skill);
                      return (
                        <tr key={skill.name} className={`hover:bg-muted/30 transition-colors ${isEditing ? "bg-sky-50 dark:bg-sky-950/20" : ""}`}>
                          <td className="px-4 py-2 font-medium">
                            <div className="flex items-center gap-2">
                              {skillIcon ? (
                                <img
                                  src={skillIcon}
                                  alt=""
                                  className="h-10 w-10 shrink-0 object-contain"
                                  style={{ imageRendering: "pixelated" }}
                                />
                              ) : null}
                              <span>{skill.name}</span>
                            </div>
                          </td>
                          <td className="px-3 py-2 text-center">
                            {numCell(skill.studioLevel, () => undefined, false)}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {numCell(skill.craftingIntelligence, () => undefined, false)}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {numCell(skill.buyPrice, () => undefined, false, "$")}
                          </td>
                          <td className="px-3 py-2 text-center">
                            {numCell(skill.sellPrice, () => undefined, false, "$")}
                          </td>
                          <td className="px-3 py-2 text-center text-xs font-medium text-muted-foreground">
                            {getSkillTypeLabel(skill) ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-center">
                            <span className={craftable ? "text-emerald-600 font-semibold" : "text-destructive font-semibold"}>
                              {craftable ? "Y" : "N"}
                            </span>
                          </td>
                          <td className="px-3 py-2 min-w-[200px]">
                            {isEditing
                              ? <Input value={d.description ?? ""} onChange={(e) => setEditDraft((x) => x ? { ...x, description: e.target.value || undefined } : x)}
                                  placeholder="Tips or notes…" className="h-7 text-sm px-2" />
                              : <div className="space-y-0.5">
                                  {d.weaponResistance && (
                                    <ToneBadge category="shop" className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold">
                                      {d.weaponResistance} Resistance
                                    </ToneBadge>
                                  )}
                                  {d.description
                                    ? <span className="block text-xs text-muted-foreground line-clamp-2">{d.description}</span>
                                    : !d.weaponResistance && <span className="text-muted-foreground/30 text-xs">—</span>}
                                </div>}
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex gap-1 justify-end">
                              {isEditing ? (
                                <>
                                  <button onClick={commitEdit} className="text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 p-0.5"><Check className="w-3.5 h-3.5" /></button>
                                  <button onClick={cancelEdit} className="text-muted-foreground hover:text-destructive p-0.5"><X className="w-3.5 h-3.5" /></button>
                                </>
                              ) : (
                                <>
                                  <button onClick={() => startEdit(skill)} className="text-muted-foreground hover:text-foreground p-0.5 transition-colors"><Pencil className="w-3 h-3" /></button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <p className="text-xs text-muted-foreground mt-3 text-center">
          {Object.keys(skills).length} skill{Object.keys(skills).length !== 1 ? "s" : ""} in database � Base data is shared game data, notes are community-added context
        </p>
      </div>
    </div>
  );
}


