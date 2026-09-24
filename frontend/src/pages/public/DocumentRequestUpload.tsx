/**
 * ARCH43-S2:page-public-request — the recipient of a missing-document request
 * uploads it here. No account: the single-use link is the credential, and
 * the page shows only the case title and the document type asked for.
 */
import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { CheckCircle2, Loader2, Upload } from "lucide-react";

import { BUTTON_PRIMARY, HINT, PAGE_TITLE, SURFACE } from "@/components/ui/primitives";
import { previewDocumentRequest, uploadRequestedDocument } from "@/services/api/cases";
import { errorMessage } from "@/services/api/errors";

const DocumentRequestUpload: React.FC = () => {
  const { token = "" } = useParams<{ token: string }>();
  const [file, setFile] = useState<File | null>(null);
  const info = useQuery({ queryKey: ["document-request", token], queryFn: () => previewDocumentRequest(token), retry: false, enabled: Boolean(token) });
  const upload = useMutation({ mutationFn: (f: File) => uploadRequestedDocument(token, f) });
  return (
    <main className="mx-auto max-w-lg p-6">
      <section className={`${SURFACE} space-y-4 p-6`}>
        <h1 className={PAGE_TITLE}>Upload a requested document</h1>
        {info.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
        {info.isError ? <p className="text-sm text-destructive">{errorMessage(info.error, "This link is not valid.")}</p> : null}
        {upload.isSuccess ? (
          <p className="flex items-center gap-2 text-sm"><CheckCircle2 className="h-4 w-4 text-green-600" aria-hidden /> Received. Thank you — this link is now closed.</p>
        ) : info.data ? (
          <>
            <p className="text-sm">
              <strong>{info.data.case_title}</strong> needs a <strong>{info.data.document_type.replace(/_/g, " ")}</strong>.
            </p>
            <p className={HINT}>This link works once and expires {new Date(info.data.expires_at).toLocaleString()}.</p>
            <input type="file" accept=".pdf,image/*" aria-label="Document" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            {upload.isError ? <p className="text-sm text-destructive">{errorMessage(upload.error, "This link is not valid.")}</p> : null}
            <button type="button" className={BUTTON_PRIMARY} disabled={!file || upload.isPending} onClick={() => file && upload.mutate(file)}>
              {upload.isPending ? <Loader2 className="mr-2 inline h-4 w-4 animate-spin" aria-hidden /> : <Upload className="mr-2 inline h-4 w-4" aria-hidden />}
              Upload
            </button>
          </>
        ) : null}
      </section>
    </main>
  );
};

export default DocumentRequestUpload;
