import React, { useState, useMemo, useCallback } from "react";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { useForm } from "react-hook-form";

import { zodResolver } from "@hookform/resolvers/zod";

import { z } from "zod";

import { Link } from "react-router-dom";

import { toast } from "sonner";

import {
  Search,
  FileText,
  RefreshCw,
  Eye,
  Trash2,
  ArrowUpDown,
  Filter,
} from "lucide-react";

import { workItemApi } from "@/services/api/workItem";

import { SkeletonTable } from "@/components/common/skeletons/SkeletonTable";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";

import { formatBytes } from "@/utils/formatters";

import { ApiError } from "@/services/api/client";

import { ROUTES } from "@/constants/routes";

import type { WorkItemStatus, WorkItemSortField } from "@/types/workItem";

/* ============================================================================
   Query Keys
============================================================================ */

const WORK_ITEMS_QUERY_KEY = ["work-items"] as const;

/* ============================================================================
   Validation Schema
============================================================================ */

const filterFormSchema = z.object({
  search: z.string().max(100, "Search query is too long.").optional(),

  status: z
    .union([
      z.literal("ALL"),
      z.literal("QUEUED"),
      z.literal("PROCESSING"),
      z.literal("COMPLETED"),
      z.literal("FAILED"),
    ])
    .optional(),
});

type FilterFormInput = z.infer<typeof filterFormSchema>;

/* ============================================================================
   Status Badge Styling
============================================================================ */

const STATUS_BADGE_MAP: Record<WorkItemStatus, string> = {
  QUEUED: "bg-primary/10 text-primary border-primary/20",

  PROCESSING: "bg-amber-500/10 text-amber-500 border-amber-500/20",

  COMPLETED: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",

  FAILED: "bg-destructive/10 text-destructive border-destructive/20",
};

/* ============================================================================
   Work Items Page
============================================================================ */

export const WorkItems: React.FC = () => {
  const queryClient = useQueryClient();

  /* ==========================================================================
       Pagination
    ========================================================================== */

  const [currentPage, setCurrentPage] = useState(1);

  const [pageSize] = useState(10);

  /* ==========================================================================
       Sorting
    ========================================================================== */

  const [sortBy, setSortBy] = useState<WorkItemSortField>("created_at");

  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  /* ==========================================================================
       Filters
    ========================================================================== */

  const [activeFilters, setActiveFilters] = useState<{
    search?: string;
    status?: WorkItemStatus;
  }>({});
  /* ==========================================================================
       Query Filters
    ========================================================================== */

  const queryFilters = useMemo(
    () => ({
      page: currentPage,
      pageSize,
      sortBy,
      sortOrder,

      ...(activeFilters.search ? { search: activeFilters.search } : {}),

      ...(activeFilters.status ? { status: activeFilters.status } : {}),
    }),
    [currentPage, pageSize, activeFilters, sortBy, sortOrder]
  );
  console.log("QUERY FILTERS:", queryFilters);

  /* ==========================================================================
       Fetch Work Items
    ========================================================================== */

  const {
    data: response,
    isLoading,
    isFetching,
    error,
  } = useQuery({
    queryKey: [...WORK_ITEMS_QUERY_KEY, queryFilters],

    queryFn: () => workItemApi.getWorkItems(queryFilters),

    placeholderData: (previousData) => previousData,

    refetchOnWindowFocus: true,

    refetchInterval: (query) => {
      const currentList = query.state.data;

      if (!currentList) {
        return false;
      }

      if (!Array.isArray(currentList.items)) {
        return false;
      }

      const hasRunningJobs = currentList.items.some(
        (item) =>
          item.status === "QUEUED" ||
          item.status === "PROCESSING"
      );

      return hasRunningJobs ? 2000 : false;
    },
  });

  /* ==========================================================================
       Reprocess Mutation
    ========================================================================== */

  const { mutate: triggerReprocess, isPending: isReprocessing } = useMutation({
    mutationFn: workItemApi.reprocessWorkItem,

    onSuccess: async () => {
      toast.success("Document has been queued for reprocessing.");

      await queryClient.invalidateQueries({
        queryKey: WORK_ITEMS_QUERY_KEY,
      });
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message ?? "Unable to schedule reprocessing.");

        return;
      }

      toast.error("Unexpected network error.");
    },
  });

  /* ==========================================================================
       Delete Mutation
    ========================================================================== */

  const { mutate: triggerDelete, isPending: isDeleting } = useMutation({
    mutationFn: workItemApi.deleteWorkItem,

    onSuccess: async () => {
      toast.success("Document has been deleted.");

      await queryClient.invalidateQueries({
        queryKey: WORK_ITEMS_QUERY_KEY,
      });
    },

    onError: (error: unknown) => {
      if (error instanceof ApiError) {
        toast.error(error.message ?? "Unable to delete document.");

        return;
      }

      toast.error("Unexpected network error.");
    },
  });

  /* ==========================================================================
       Filter Form
    ========================================================================== */

  const { register, handleSubmit } = useForm<FilterFormInput>({
    resolver: zodResolver(filterFormSchema),

    defaultValues: {
      search: "",
      status: "ALL",
    },
  });
  /* ==========================================================================
       Filter Submit
    ========================================================================== */

  const handleApplyFiltersSubmit = useCallback(
    (data: FilterFormInput): void => {
      // Reset pagination whenever filters change
      console.log("FORM DATA:", data);
      setCurrentPage(1);

      const nextFilters: {
        search?: string;
        status?: WorkItemStatus;
      } = {};

      const search = data.search?.trim();

      if (search) {
        nextFilters.search = search;
      }

      if (data.status && data.status !== "ALL") {
        nextFilters.status = data.status;
      }
      console.log("NEXT FILTERS:", nextFilters);

      setActiveFilters(nextFilters);
    },
    []
  );


  /* ==========================================================================
       Sorting
    ========================================================================== */

  const handleSortToggle = useCallback(
    (field: WorkItemSortField): void => {
      if (sortBy === field) {
        setSortOrder((previous) => (previous === "asc" ? "desc" : "asc"));
      } else {
        setSortBy(field);
        setSortOrder("desc");
      }

      setCurrentPage(1);
    },
    [sortBy]
  );

  /* ==========================================================================
       Derived Response Data
    ========================================================================== */

  const items = response?.items ?? [];

  const totalPages = response?.totalPages ?? 1;

  /* ==========================================================================
       Pagination Handlers
    ========================================================================== */

  const handlePreviousPage = useCallback((): void => {
    setCurrentPage((previous) => Math.max(1, previous - 1));
  }, []);

  const handleNextPage = useCallback((): void => {
    setCurrentPage((previous) => Math.min(totalPages, previous + 1));
  }, [totalPages]);

  /* ==========================================================================
       Loading State
    ========================================================================== */

  if (isLoading && !response) {
    return (
      <div className="space-y-6">
        <div className="space-y-1 select-none">
          <h2 className="text-2xl font-extrabold tracking-tight">
            Documents Pipeline
          </h2>

          <div className="h-4 w-96 rounded bg-muted/40 animate-pulse" />
        </div>

        <SkeletonTable rows={10} />
      </div>
    );
  }

  /* ==========================================================================
       Error State
    ========================================================================== */

  if (error) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-6">
        <ErrorState
          title="Failed to load documents"
          description="An error occurred while loading your document collection."
          onRetry={async () => {
            await queryClient.invalidateQueries({
              queryKey: WORK_ITEMS_QUERY_KEY,
            });
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ======================================================
            Page Header
        ====================================================== */}

      <div className="space-y-1 select-none">
        <h2 className="text-2xl font-extrabold tracking-tight">
          Documents Database
        </h2>

        <p className="text-sm font-semibold leading-relaxed text-muted-foreground">
          Monitor ingestion pipelines, search uploaded documents, inspect AI
          processing status, and navigate into detailed extraction results.
        </p>
      </div>

      {/* ======================================================
            Filters
        ====================================================== */}

      <form
        onSubmit={handleSubmit(handleApplyFiltersSubmit)}
        noValidate
        className="grid grid-cols-1 gap-4 rounded-xl border border-border/40 bg-card p-4 shadow-sm sm:grid-cols-12"
      >
        {/* Search */}

        <div className="relative sm:col-span-6">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />

          <input
            {...register("search")}
            type="text"
            placeholder="Search documents..."
            className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-4 text-sm font-semibold transition-all placeholder:text-muted-foreground/70 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>

        {/* Status */}

        <div className="relative sm:col-span-4">
          <select
            {...register("status")}
            className="w-full cursor-pointer appearance-none rounded-lg border border-border bg-background px-3 py-2 text-sm font-semibold transition-all focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="ALL">All Statuses</option>

            <option value="QUEUED">Queued</option>

            <option value="PROCESSING">Processing</option>

            <option value="COMPLETED">Completed</option>

            <option value="FAILED">Failed</option>
          </select>

          <Filter className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        </div>

        {/* Apply */}

        <button
          type="submit"
          className="flex items-center justify-center space-x-2 rounded-lg bg-primary px-4 py-2 text-xs font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/95 active:scale-[0.98] sm:col-span-2"
        >
          <Search className="h-3.5 w-3.5" />

          <span>Apply Filters</span>
        </button>
      </form>

      {/* ======================================================
            Documents Table
        ====================================================== */}

      <div className="relative overflow-hidden rounded-xl border border-border/60 bg-card shadow-sm dark:border-border/40">
        {isFetching && (
          <div className="absolute inset-x-0 top-0 h-0.5 overflow-hidden bg-primary/20">
            <div className="h-full w-full animate-pulse bg-primary" />
          </div>
        )}
        <div className="overflow-x-auto">
          {items.length === 0 ? (
            <div className="p-8">
              <EmptyState
                icon={FileText}
                title="No documents found"
                description="No documents match the current search and filter criteria."
              />
            </div>
          ) : (
            <table className="w-full table-fixed border-collapse text-left">
              <thead>
                <tr className="border-b border-border/40 bg-muted/20 text-xs font-bold uppercase tracking-wider text-muted-foreground dark:bg-muted/5">
                  {/* ======================================================
                        Document Name
                    ====================================================== */}
                  <th
                    className="w-5/12 p-4"
                    aria-sort={
                      sortBy === "original_filename"
                        ? sortOrder === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      disabled={isFetching}
                      onClick={() => handleSortToggle("original_filename")}
                      className="flex items-center space-x-1.5 font-bold uppercase transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                    >
                      <span>Document Filename</span>

                      <ArrowUpDown className="h-3 w-3" />
                    </button>
                  </th>

                  {/* ======================================================
                        File Type
                    ====================================================== */}
                  <th className="w-2/12 p-4">Format</th>

                  {/* ======================================================
                        File Size
                    ====================================================== */}
                  <th
                    className="w-2/12 p-4"
                    aria-sort={
                      sortBy === "file_size"
                        ? sortOrder === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      disabled={isFetching}
                      onClick={() => handleSortToggle("file_size")}
                      className="flex items-center space-x-1.5 font-bold uppercase transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                    >
                      <span>File Size</span>

                      <ArrowUpDown className="h-3 w-3" />
                    </button>
                  </th>

                  {/* ======================================================
                        Status
                    ====================================================== */}
                  <th className="w-2/12 p-4">Status</th>

                  {/* ======================================================
                        Actions
                    ====================================================== */}
                  <th className="w-1/12 p-4 text-right">Actions</th>
                </tr>
              </thead>

              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.id}
                    className="border-b border-border/40 text-sm last:border-b-0"
                  >
                    {/* ======================================================
                          Filename
                      ====================================================== */}

                    <td className="p-4">
                      <div className="flex items-center space-x-3.5">
                        <FileText className="h-5 w-5 flex-shrink-0 text-primary/80" />

                        <span
                          className="truncate font-bold text-foreground/90"
                          title={item.original_filename}
                        >
                          {item.original_filename}
                        </span>
                      </div>
                    </td>

                    {/* ======================================================
                          File Type
                      ====================================================== */}

                    <td className="p-4 font-semibold text-muted-foreground">
                      {item.file_type.split("/")[1]?.toUpperCase() ?? "UNKNOWN"}
                    </td>

                    {/* ======================================================
                          File Size
                      ====================================================== */}

                    <td className="p-4 text-xs font-bold text-muted-foreground">
                      {formatBytes(item.file_size)}
                    </td>
                    {/* ======================================================
                          Status
                      ====================================================== */}

                    <td className="p-4 select-none">
                      {/*
                          TODO (Future Module):
                          Replace with reusable <StatusBadge />
                        */}

                      <span
                        className={`
                            inline-flex
                            items-center
                            rounded-full
                            border
                            px-2
                            py-0.5
                            text-[10px]
                            font-black
                            uppercase
                            leading-none
                            tracking-wide
                            ${STATUS_BADGE_MAP[item.status]}
                          `}
                      >
                        {item.status.toLowerCase()}
                      </span>
                    </td>

                    {/* ======================================================
                          Actions
                      ====================================================== */}

                    <td className="p-4">
                      <div className="flex items-center justify-end space-x-2">
                        {item.status === "FAILED" ? (
                          <button
                            type="button"
                            disabled={isReprocessing}
                            onClick={() => triggerReprocess(item.id)}
                            title="Retry Processing"
                            className="
                                rounded-md
                                border
                                border-amber-500/20
                                bg-amber-500/10
                                p-1.5
                                text-amber-500
                                transition-all
                                hover:bg-amber-500/20
                                active:scale-[0.96]
                                disabled:pointer-events-none
                                disabled:opacity-50
                              "
                          >
                            <RefreshCw className="h-4 w-4" />
                          </button>
                        ) : (
                          <Link
                            to={ROUTES.WORK_ITEM_DETAILS.replace(
                              ":id",
                              item.id
                            )}
                            title="View Details"
                            className="
                                rounded-md
                                border
                                border-border
                                bg-background
                                p-1.5
                                text-muted-foreground
                                transition-all
                                hover:bg-muted
                                hover:text-foreground
                                active:scale-[0.96]
                              "
                          >
                            <Eye className="h-4 w-4" />
                          </Link>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {/* ======================================================
            Pagination
        ====================================================== */}

      {totalPages > 1 && (
        <footer className="flex items-center justify-between select-none">
          <p className="text-xs font-bold text-muted-foreground">
            Page {currentPage} of {totalPages}
          </p>

          <div className="flex items-center space-x-2">
            <button
              type="button"
              onClick={handlePreviousPage}
              disabled={currentPage === 1}
              className="
                  rounded-lg
                  border
                  border-border
                  bg-card
                  px-3.5
                  py-1.5
                  text-xs
                  font-black
                  uppercase
                  transition-all
                  hover:bg-muted
                  active:scale-[0.98]
                  disabled:pointer-events-none
                  disabled:opacity-40
                "
            >
              Previous
            </button>

            <button
              type="button"
              onClick={handleNextPage}
              disabled={currentPage === totalPages}
              className="
                  rounded-lg
                  border
                  border-border
                  bg-card
                  px-3.5
                  py-1.5
                  text-xs
                  font-black
                  uppercase
                  transition-all
                  hover:bg-muted
                  active:scale-[0.98]
                  disabled:pointer-events-none
                  disabled:opacity-40
                "
            >
              Next
            </button>
          </div>
        </footer>
      )}
    </div>
  );
};

WorkItems.displayName = "WorkItems";

export default WorkItems;
