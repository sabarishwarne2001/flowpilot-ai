/**
 * ARCH47-S2:ready-to-post — approved outcomes (reconciled invoices, confirmed
 * tables, completed cases) and, per active target, whether each object is in
 * the ledger yet. "Post" plans postings through the ledger's idempotent insert:
 * pressing it twice, or after a Flow Builder rule already posted, finds the
 * existing posting instead of making a second one.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Eye, Loader2, Send } from "lucide-react";

import { PostingStateBadge, sourcePath } from "@/components/erp/common";
import { PreviewPanel } from "@/components/erp/PreviewPanel";
import { BUTTON_GHOST, BUTTON_PRIMARY, HINT, SELECT, SURFACE } from "@/components/ui/primitives";
import { erpPostingPath } from "@/routes/tenantPaths";
import { createPostings, erpKeys, listOutcomes, previewPosting } from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import {
  OBJECT_LABELS, SOURCE_LABELS, type ObjectKind, type OutcomeRow, type PostResponse, type PreviewResponse, type TargetRow,
} from "@/types/erp";
import { formatTimestamp } from "@/utils/displayTime";

interface OutcomeCardProps {
  readonly workspaceId: string;
  readonly outcome: OutcomeRow;
  readonly targets: readonly TargetRow[];
  readonly canPost: boolean;
}

const OutcomeCard: React.FC<OutcomeCardProps> = ({ workspaceId, outcome, targets, canPost }) => {
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const queryClient = useQueryClient();
  const [picked, setTargetId] = useState(targets[0]?.id ?? "");
  const target = targets.find((t) => t.id === picked) ?? targets[0];
  const targetId = target?.id ?? "";
  const available = useMemo(
    () => outcome.objects.filter((k) => target?.objects.includes(k)),
    [outcome.objects, target],
  );
  const [chosen, setChosen] = useState<readonly ObjectKind[]>([]);
  const kinds = chosen.filter((k) => available.includes(k));
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [result, setResult] = useState<PostResponse | null>(null);

  const post = useMutation({
    mutationFn: () => createPostings(workspaceId, { target_id: targetId, source_kind: outcome.kind, source_id: outcome.id, object_kinds: kinds }),
    onSuccess: (data) => {
      setResult(data);
      void queryClient.invalidateQueries({ queryKey: erpKeys.all(workspaceId) });
    },
  });
  const look = useMutation({
    mutationFn: (kind: ObjectKind) => previewPosting(workspaceId, { target_id: targetId, source_kind: outcome.kind, source_id: outcome.id, object_kind: kind }),
    onSuccess: setPreview,
  });
  const stateOf = (kind: ObjectKind) => outcome.postings.find((p) => p.target_id === targetId && p.object_kind === kind);
  const toggle = (kind: ObjectKind): void =>
    setChosen((current) => (current.includes(kind) ? current.filter((k) => k !== kind) : [...current, kind]));

  return (
    <article className={`${SURFACE} space-y-2 p-3`}>
      <header className="flex flex-wrap items-center gap-2">
        <Link className="font-semibold hover:underline" to={sourcePath(orgSlug, workspaceSlug, outcome.kind, outcome.id)}>
          {outcome.label}
        </Link>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-semibold">{SOURCE_LABELS[outcome.kind]}</span>
        <span className={HINT}>Approved {formatTimestamp(outcome.approved_at)}</span>
      </header>
      {targets.length === 0 ? (
        <p className={HINT}>Add an active target to post this.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select className={`${SELECT} w-64`} aria-label="Target" value={targetId}
            onChange={(e) => { setTargetId(e.target.value); setPreview(null); setResult(null); }}>
            {targets.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          {available.length === 0 ? <span className={HINT}>This target takes none of this outcome&apos;s objects.</span> : null}
          {available.map((kind) => {
            const existing = stateOf(kind);
            return (
              <span key={kind} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2 py-1 text-xs">
                <input type="checkbox" aria-label={`Post ${OBJECT_LABELS[kind]}`} disabled={!canPost || Boolean(existing?.posting_id)}
                  checked={kinds.includes(kind)} onChange={() => toggle(kind)} />
                {OBJECT_LABELS[kind]}
                {existing?.posting_id ? (
                  <Link to={erpPostingPath(orgSlug, workspaceSlug, existing.posting_id)}><PostingStateBadge state={existing.state} /></Link>
                ) : <PostingStateBadge state={null} />}
                {canPost ? (
                  <button type="button" className="text-muted-foreground hover:text-foreground" aria-label={`Preview ${OBJECT_LABELS[kind]}`}
                    disabled={look.isPending} onClick={() => look.mutate(kind)}>
                    <Eye className="h-3.5 w-3.5" aria-hidden />
                  </button>
                ) : null}
              </span>
            );
          })}
          {canPost ? (
            <button type="button" className={`${BUTTON_PRIMARY} ml-auto`} disabled={kinds.length === 0 || post.isPending} onClick={() => post.mutate()}>
              {post.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Send className="h-4 w-4" aria-hidden />}
              Post {kinds.length > 0 ? kinds.length : ""}
            </button>
          ) : null}
        </div>
      )}
      {post.isError ? <p className="text-sm text-destructive">{errorMessage(post.error, "Could not post.")}</p> : null}
      {look.isError ? <p className="text-sm text-destructive">{errorMessage(look.error, "Could not preview.")}</p> : null}
      {result ? (
        <ul className="space-y-1 text-sm">
          {result.results.map((r) => (
            <li key={r.object_kind}>
              {OBJECT_LABELS[r.object_kind]}:{" "}
              {r.error ? <span className="text-destructive">{r.error}</span> : (
                <>
                  {r.created ? "planned" : "already in the ledger"} <PostingStateBadge state={r.state} />
                  {r.posting_id ? <> <Link className="text-primary hover:underline" to={erpPostingPath(orgSlug, workspaceSlug, r.posting_id)}>open</Link></> : null}
                </>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {preview ? (
        <div className="space-y-1">
          <button type="button" className={BUTTON_GHOST} onClick={() => setPreview(null)}>Close preview</button>
          <PreviewPanel preview={preview} />
        </div>
      ) : null}
    </article>
  );
};

export const ReadyToPost: React.FC<{ readonly workspaceId: string; readonly targets: readonly TargetRow[]; readonly canPost: boolean }> = ({
  workspaceId, targets, canPost,
}) => {
  const query = useQuery({
    queryKey: erpKeys.outcomes(workspaceId),
    queryFn: () => listOutcomes(workspaceId),
    enabled: Boolean(workspaceId),
  });
  const active = targets.filter((t) => t.status === "ACTIVE");
  if (query.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (query.isError) {
    return <p className="text-sm text-destructive">{errorMessage(query.error, "Could not load approved outcomes.")}</p>;
  }
  const items = query.data?.items ?? [];
  return (
    <div className="space-y-3">
      <p className={HINT}>
        Only approved outcomes are listed: three-way matches that were approved, tables a person accepted, and cases
        that completed. Targets with auto-post on post new ones by themselves.
      </p>
      {items.length === 0 ? <p className={HINT}>Nothing approved to post yet.</p> : null}
      {items.map((outcome) => (
        <OutcomeCard key={`${outcome.kind}:${outcome.id}`} workspaceId={workspaceId} outcome={outcome} targets={active} canPost={canPost} />
      ))}
    </div>
  );
};

export default ReadyToPost;
