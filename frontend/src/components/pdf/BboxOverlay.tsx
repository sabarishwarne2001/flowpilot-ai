import React, { useMemo } from "react";

import type {
  CitationBoundingBox,
  CitationSource,
} from "@/services/streaming/resumableStream";

export interface NormalizedRect {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

export const toNormalizedRect = (
  bbox: CitationBoundingBox,
  pageWidthPt: number,
  pageHeightPt: number,
): NormalizedRect | null => {
  const { x0, y0, x1, y1, space } = bbox;

  if (
    !Number.isFinite(x0) ||
    !Number.isFinite(y0) ||
    !Number.isFinite(x1) ||
    !Number.isFinite(y1)
  ) {
    return null;
  }

  let refWidth: number;
  let refHeight: number;

  switch (space) {
    case "normalized":
      refWidth = 1;
      refHeight = 1;
      break;

    case "points":
      refWidth = bbox.width ?? pageWidthPt;
      refHeight = bbox.height ?? pageHeightPt;
      break;

    case "pixels":
    default:
      if (!bbox.width || !bbox.height) {
        return null;
      }
      refWidth = bbox.width;
      refHeight = bbox.height;
      break;
  }

  if (refWidth <= 0 || refHeight <= 0) {
    return null;
  }

  const left = x0 / refWidth;
  const top = y0 / refHeight;
  const width = (x1 - x0) / refWidth;
  const height = (y1 - y0) / refHeight;

  if (left > 1 || top > 1 || left + width < 0 || top + height < 0) {
    return null;
  }

  const clampedLeft = Math.max(0, Math.min(1, left));
  const clampedTop = Math.max(0, Math.min(1, top));

  return {
    left: clampedLeft,
    top: clampedTop,
    width: Math.max(0, Math.min(1 - clampedLeft, width)),
    height: Math.max(0, Math.min(1 - clampedTop, height)),
  };
};

export interface OverlayBox {
  readonly source: CitationSource;
  readonly active: boolean;
}

export interface BboxOverlayProps {
  readonly boxes: readonly OverlayBox[];
  readonly pageWidthPt: number;
  readonly pageHeightPt: number;
  readonly onBoxClick?: (source: CitationSource) => void;
}

export const BboxOverlay: React.FC<BboxOverlayProps> = ({
  boxes,
  pageWidthPt,
  pageHeightPt,
  onBoxClick,
}) => {
  const rendered = useMemo(
    () =>
      boxes
        .map((box) => {
          if (!box.source.bbox) {
            return null;
          }
          const rect = toNormalizedRect(
            box.source.bbox,
            pageWidthPt,
            pageHeightPt,
          );
          return rect ? { ...box, rect } : null;
        })
        .filter(
          (entry): entry is OverlayBox & { rect: NormalizedRect } =>
            entry !== null,
        ),
    [boxes, pageWidthPt, pageHeightPt],
  );

  if (rendered.length === 0) {
    return null;
  }

  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden={!onBoxClick}>
      {rendered.map(({ source, active, rect }) => (
        <button
          key={source.chunk_id}
          type="button"
          disabled={!onBoxClick}
          onClick={onBoxClick ? () => onBoxClick(source) : undefined}
          aria-label={`Cited passage from ${source.original_filename}${
            source.page_number !== null ? `, page ${source.page_number}` : ""
          }`}
          className={[
            "pointer-events-auto absolute rounded-[2px] transition-colors duration-150",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1",
            active
              ? "bg-amber-400/35 ring-2 ring-amber-500"
              : "bg-amber-300/20 ring-1 ring-amber-400/50 hover:bg-amber-300/30",
            onBoxClick ? "cursor-pointer" : "cursor-default",
          ].join(" ")}
          style={{
            left: `${rect.left * 100}%`,
            top: `${rect.top * 100}%`,
            width: `${rect.width * 100}%`,
            height: `${rect.height * 100}%`,
          }}
        />
      ))}
    </div>
  );
};

export default BboxOverlay;
