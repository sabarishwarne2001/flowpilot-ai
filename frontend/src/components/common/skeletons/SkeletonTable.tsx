import React from "react";

interface SkeletonTableProps {
  readonly rows?: number;
  readonly className?: string;
}

export const SkeletonTable: React.FC<SkeletonTableProps> = ({
  rows = 5,
  className = "",
}) => {
  const rowCount = Math.max(1, Math.min(20, rows));

  const dummyRows = Array.from({
    length: rowCount,
  });

  return (
    <div
      role="presentation"
      aria-hidden="true"
      aria-label="Loading table"
      className={`pointer-events-none overflow-hidden overflow-y-hidden fp-card select-none ${className}`}
    >
      {/* ===========================
          Toolbar
      =========================== */}

      <div className="flex flex-col items-center justify-between space-y-3 border-b border-border/40 bg-muted/5 p-4 sm:flex-row sm:space-x-4 sm:space-y-0">
        <div className="h-9 w-full fp-skeleton rounded-lg sm:w-72" />

        <div className="flex w-full justify-end space-x-2 sm:w-auto">
          <div className="h-9 w-20 fp-skeleton rounded-lg" />

          <div className="h-9 w-24 fp-skeleton rounded-lg" />
        </div>
      </div>

      {/* ===========================
          Table
      =========================== */}

      <div className="w-full overflow-x-auto">
        <table className="w-full table-fixed border-collapse text-left">
          <thead>
            <tr className="border-b border-border/40 bg-muted/20 dark:bg-muted/5">
              <th className="w-2/5 p-4">
                <div className="h-3.5 w-1/4 fp-skeleton rounded" />
              </th>

              <th className="w-1/5 p-4">
                <div className="h-3.5 w-1/3 fp-skeleton rounded" />
              </th>

              <th className="w-1/5 p-4">
                <div className="h-3.5 w-1/2 fp-skeleton rounded" />
              </th>

              <th className="w-1/5 p-4 text-right">
                <div className="ml-auto h-3.5 w-1/3 fp-skeleton rounded" />
              </th>
            </tr>
          </thead>

          <tbody>
            {dummyRows.map((_, index) => (
              <tr
                key={`skeleton-${index}`}
                className="border-b border-border/40 last:border-b-0"
              >
                <td className="flex items-center space-x-3 p-4">
                  <div className="h-8 w-8 flex-shrink-0 fp-skeleton rounded" />

                  <div className="h-3.5 w-3/5 fp-skeleton rounded" />
                </td>

                <td className="p-4">
                  <div className="h-3 w-1/2 fp-skeleton rounded" />
                </td>
                <td className="p-4">
                  <div className="h-3 w-2/3 fp-skeleton rounded" />
                </td>

                <td className="p-4 text-right">
                  <div className="ml-auto h-3 w-1/4 fp-skeleton rounded" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ===========================
          Pagination
      =========================== */}

      <div className="flex items-center justify-between border-t border-border/40 bg-muted/5 p-4">
        <div className="h-3 w-36 fp-skeleton rounded" />

        <div className="flex space-x-2">
          <div className="h-8 w-16 fp-skeleton rounded-lg" />

          <div className="h-8 w-16 fp-skeleton rounded-lg" />
        </div>
      </div>
    </div>
  );
};

export default SkeletonTable;
