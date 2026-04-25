import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentRealtimeEvent } from "@/lib/agentRealtime";

const APPROVE_DISMISS_TIMEOUT_MS = 30_000;

type DraftPlanState = {
  request_key: string;
  items: Array<{ id: string; text: string }>;
  isReady: boolean;
  isApproving: boolean;
} | null;

export function useDraftPlan({
  stopRequestedRef,
}: {
  stopRequestedRef: React.MutableRefObject<boolean>;
}) {
  const [draftPlan, setDraftPlan] = useState<DraftPlanState>(null);
  const approvalTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearDraftPlan = useCallback(() => {
    if (approvalTimeoutRef.current) {
      clearTimeout(approvalTimeoutRef.current);
      approvalTimeoutRef.current = null;
    }
    setDraftPlan(null);
  }, []);

  const handleDraftPlanEvent = useCallback(
    (event: AgentRealtimeEvent) => {
      if (event.type === "draft_plan.start") {
        // Only the stop button should suppress a new plan; ignoreAssistantEventsRef
        // is for the typewriter animation and must not affect plan events.
        if (stopRequestedRef.current) {
          console.log("[draft_plan] start dropped — stop requested", { request_key: event.request_key });
          return;
        }
        console.log("[draft_plan] start", { request_key: event.request_key });
        setDraftPlan({ request_key: event.request_key, items: [], isReady: false, isApproving: false });
        return;
      }
      if (event.type === "draft_plan.item") {
        if (stopRequestedRef.current) return;
        console.log("[draft_plan] item", { request_key: event.request_key, item: event.item });
        setDraftPlan((current) => {
          if (!current || current.request_key !== event.request_key) return current;
          return { ...current, items: [...current.items, event.item] };
        });
        return;
      }
      if (event.type === "draft_plan.ready") {
        if (stopRequestedRef.current) return;
        console.log("[draft_plan] ready", { request_key: event.request_key });
        setDraftPlan((current) => {
          if (!current || current.request_key !== event.request_key) return current;
          return { ...current, isReady: true };
        });
        return;
      }
      if (event.type === "draft_plan.approved") {
        console.log("[draft_plan] approved — clearing card", { request_key: event.request_key });
        if (approvalTimeoutRef.current) {
          clearTimeout(approvalTimeoutRef.current);
          approvalTimeoutRef.current = null;
        }
        setDraftPlan((current) => {
          if (!current || current.request_key !== event.request_key) return current;
          return null;
        });
        return;
      }
    },
    // Refs are stable objects; reading .current inside is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const startApproving = useCallback((requestKey: string, onApprove: () => void) => {
    setDraftPlan((current) => {
      if (!current || current.request_key !== requestKey || current.isApproving) return current;
      return { ...current, isApproving: true };
    });
    onApprove();
    // Auto-dismiss after 30s if the server never sends draft_plan.approved
    approvalTimeoutRef.current = setTimeout(() => {
      setDraftPlan((current) => {
        if (!current || current.request_key !== requestKey) return current;
        return null;
      });
      approvalTimeoutRef.current = null;
    }, APPROVE_DISMISS_TIMEOUT_MS);
  }, []);

  useEffect(() => {
    return () => {
      if (approvalTimeoutRef.current) clearTimeout(approvalTimeoutRef.current);
    };
  }, []);

  return { draftPlan, handleDraftPlanEvent, clearDraftPlan, startApproving };
}
