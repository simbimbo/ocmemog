import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";

const DEFAULT_ENDPOINT = "http://127.0.0.1:17891";
const DEFAULT_TIMEOUT_MS = 30_000;

type PluginConfig = {
  endpoint: string;
  timeoutMs: number;
  token?: string;
};

const AUTO_HYDRATION_ENABLED = ["1", "true", "yes"].includes(
  String(process.env.OCMEMOG_AUTO_HYDRATION ?? "false").trim().toLowerCase(),
);

type SearchResponse = {
  ok: boolean;
  mode?: string;
  warnings?: string[];
  missingDeps?: string[];
  todo?: string[];
  results?: Array<{
    reference: string;
    bucket?: string;
    score?: number;
    content?: string;
  }>;
  error?: string;
};

type GetResponse = {
  ok: boolean;
  mode?: string;
  warnings?: string[];
  missingDeps?: string[];
  todo?: string[];
  reference?: string;
  memory?: Record<string, unknown>;
  error?: string;
};

type RecentResponse = {
  ok: boolean;
  mode?: string;
  warnings?: string[];
  missingDeps?: string[];
  todo?: string[];
  categories?: string[];
  since?: string | null;
  limit?: number;
  results?: Record<string, Array<{ reference: string; timestamp?: string; content?: string }>>;
  error?: string;
};

type ConversationHydrateResponse = {
  ok: boolean;
  recent_turns?: Array<Record<string, unknown>>;
  linked_memories?: Array<{ reference?: string; content?: string }>;
  summary?: Record<string, unknown>;
  state?: Record<string, unknown>;
  error?: string;
};

type ConversationCheckpointResponse = {
  ok: boolean;
  checkpoint?: Record<string, unknown>;
  error?: string;
};

type ConversationScope = {
  conversation_id?: string;
  session_id?: string;
  thread_id?: string;
};

function readConfig(raw: unknown): PluginConfig {
  const cfg = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    endpoint: typeof cfg.endpoint === "string" && cfg.endpoint.trim() ? cfg.endpoint.trim() : DEFAULT_ENDPOINT,
    timeoutMs:
      typeof cfg.timeoutMs === "number" && Number.isFinite(cfg.timeoutMs) && cfg.timeoutMs > 0
        ? cfg.timeoutMs
        : DEFAULT_TIMEOUT_MS,
    token: typeof cfg.token === "string" && cfg.token.trim() ? cfg.token.trim() : undefined,
  };
}

async function postJson<T>(config: PluginConfig, path: string, body: Record<string, unknown>): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (config.token) {
      headers["x-ocmemog-token"] = config.token;
    }

    const response = await fetch(new URL(path, config.endpoint).toString(), {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const payload = (await response.json()) as T;
    if (!response.ok) {
      throw new Error(`sidecar returned HTTP ${response.status}`);
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

function formatWarnings(payload: { mode?: string; warnings?: string[]; missingDeps?: string[]; todo?: string[] }): string {
  const lines: string[] = [];
  if (payload.mode) {
    lines.push(`mode: ${payload.mode}`);
  }
  if (payload.warnings?.length) {
    lines.push(`warnings: ${payload.warnings.join(" | ")}`);
  }
  if (payload.missingDeps?.length) {
    lines.push(`missing deps: ${payload.missingDeps.join(" | ")}`);
  }
  if (payload.todo?.length) {
    lines.push(`todo: ${payload.todo.join(" | ")}`);
  }
  return lines.length ? `\n\n${lines.join("\n")}` : "";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    const text = asString(value);
    if (text) {
      return text;
    }
  }
  return "";
}

function extractMessageText(message: unknown): string {
  const msg = asRecord(message);
  if (!msg) {
    return "";
  }
  if (typeof msg.content === "string") {
    return msg.content.trim();
  }
  if (Array.isArray(msg.content)) {
    const text = msg.content
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        const record = asRecord(item);
        if (!record) {
          return "";
        }
        return firstString(record.text, record.content, record.input_text, record.output_text);
      })
      .filter(Boolean)
      .join("\n")
      .trim();
    if (text) {
      return text;
    }
  }
  return firstString(msg.text, msg.message, msg.output);
}

function extractRole(message: unknown): string {
  const msg = asRecord(message);
  if (!msg) {
    return "";
  }
  return firstString(msg.role, msg.type).toLowerCase();
}

function extractMessageId(message: unknown): string {
  const msg = asRecord(message);
  if (!msg) {
    return "";
  }
  return firstString(msg.id, msg.messageId, msg.message_id);
}

function extractTimestamp(message: unknown): string | undefined {
  const msg = asRecord(message);
  if (!msg) {
    return undefined;
  }
  const raw = msg.timestamp ?? msg.ts ?? msg.createdAt ?? msg.created_at;
  if (typeof raw === "string") {
    return raw;
  }
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return new Date(raw).toISOString();
  }
  return undefined;
}

function pickScopeFromValue(value: unknown): Partial<ConversationScope> {
  const record = asRecord(value);
  if (!record) {
    return {};
  }
  return {
    conversation_id: firstString(record.conversation_id, record.conversationId),
    session_id: firstString(record.session_id, record.sessionId),
    thread_id: firstString(record.thread_id, record.threadId),
  };
}

function extractConversationScope(message: unknown, sessionFallback?: string): ConversationScope {
  const msg = asRecord(message) ?? {};
  const metadata = asRecord(msg.metadata);
  const direct = pickScopeFromValue(msg);
  const metaScope = pickScopeFromValue(metadata);
  return {
    conversation_id: direct.conversation_id || metaScope.conversation_id || undefined,
    session_id: direct.session_id || metaScope.session_id || sessionFallback || undefined,
    thread_id: direct.thread_id || metaScope.thread_id || undefined,
  };
}

function mergeScope(base: ConversationScope, next: Partial<ConversationScope>): ConversationScope {
  return {
    conversation_id: base.conversation_id || next.conversation_id,
    session_id: base.session_id || next.session_id,
    thread_id: base.thread_id || next.thread_id,
  };
}

function resolveHydrationScope(messages: unknown[], ctx: { sessionKey?: string; sessionId?: string }): ConversationScope {
  let scope: ConversationScope = {
    session_id: firstString(ctx.sessionKey, ctx.sessionId) || undefined,
  };
  for (const message of [...messages].reverse()) {
    scope = mergeScope(scope, extractConversationScope(message, scope.session_id));
    if (scope.conversation_id && scope.session_id && scope.thread_id) {
      break;
    }
  }
  return scope;
}

function summarizeList(items: unknown, limit = 3): string[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .map((item) => {
      const record = asRecord(item);
      return record ? firstString(record.summary, record.content, record.reference) : "";
    })
    .filter(Boolean)
    .slice(0, limit);
}

const INTERNAL_CONTINUITY_MARKERS = [
  "Memory continuity (auto-hydrated by ocmemog):",
  "Pre-compaction memory flush.",
  "Current time:",
  "Latest user ask:",
  "Last assistant commitment:",
  "Open loops:",
  "Pending actions:",
  "Recent turns:",
  "Linked memories:",
  "Sender (untrusted metadata):",
];

function sanitizeContinuityNoise(text: string, maxLen = 280): string {
  if (!text) {
    return "";
  }
  let cleaned = text;
  for (const marker of INTERNAL_CONTINUITY_MARKERS) {
    cleaned = cleaned.split(marker).join(" ");
  }
  cleaned = cleaned
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\b(Memory continuity|Pre-compaction memory flush|Recent turns|Pending actions|Open loops|Linked memories)\b:?/gi, " ")
    .replace(/\s*\|\s*/g, " | ")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length > maxLen) {
    cleaned = `${cleaned.slice(0, maxLen - 1).trim()}…`;
  }
  return cleaned;
}

function buildHydrationContext(payload: ConversationHydrateResponse): string {
  if (!payload.ok) {
    return "";
  }
  const summary = asRecord(payload.summary);
  const state = asRecord(payload.state);
  const lines: string[] = [];

  const checkpoint = asRecord(summary?.latest_checkpoint);
  const checkpointSummary = sanitizeContinuityNoise(firstString(checkpoint?.summary), 140);
  if (checkpointSummary) {
    lines.push(`Checkpoint: ${checkpointSummary}`);
  }

  const latestUserAsk = asRecord(summary?.latest_user_ask);
  const latestUserAskText = sanitizeContinuityNoise(
    firstString(latestUserAsk?.effective_content, latestUserAsk?.content, state?.latest_user_ask),
    220,
  );
  if (latestUserAskText) {
    lines.push(`Latest user ask: ${latestUserAskText}`);
  }

  const commitment = asRecord(summary?.last_assistant_commitment);
  const commitmentText = sanitizeContinuityNoise(firstString(commitment?.content, state?.last_assistant_commitment), 180);
  if (commitmentText) {
    lines.push(`Last assistant commitment: ${commitmentText}`);
  }

  const openLoops = summarizeList(summary?.open_loops, 2).map((item) => sanitizeContinuityNoise(item, 120)).filter(Boolean);
  if (openLoops.length) {
    lines.push(`Open loops: ${openLoops.join(" | ")}`);
  }

  if (!lines.length) {
    return "";
  }
  return `Memory continuity (auto-hydrated by ocmemog):\n- ${lines.join("\n- ")}`;
}

function buildTurnMetadata(message: unknown, ctx: { agentId?: string; sessionKey?: string }) {
  const msg = asRecord(message) ?? {};
  const metadata = asRecord(msg.metadata) ?? {};
  return {
    ...metadata,
    role: extractRole(message) || undefined,
    agent_id: ctx.agentId,
    session_key: ctx.sessionKey,
    reply_to_message_id: firstString(metadata.reply_to_message_id, metadata.replyToMessageId, msg.reply_to_message_id, msg.replyToMessageId) || undefined,
  };
}

function registerAutomaticContinuityHooks(api: OpenClawPluginApi, config: PluginConfig) {
  api.on("before_message_write", (event, ctx) => {
    try {
      const role = extractRole(event.message);
      if (role !== "user" && role !== "assistant") {
        return;
      }
      const content = sanitizeContinuityNoise(extractMessageText(event.message), 4000);
      if (!content) {
        return;
      }
      const scope = extractConversationScope(event.message, ctx.sessionKey);
      if (!scope.session_id && !scope.thread_id && !scope.conversation_id) {
        return;
      }
      void postJson<{ ok: boolean }>(config, "/conversation/ingest_turn", {
        ...scope,
        role,
        content,
        message_id: extractMessageId(event.message) || undefined,
        timestamp: extractTimestamp(event.message),
        source: "openclaw.before_message_write",
        metadata: buildTurnMetadata(event.message, ctx),
      }).catch((error) => {
        api.logger.warn(`ocmemog continuity ingest failed: ${error instanceof Error ? error.message : String(error)}`);
      });
    } catch (error) {
      api.logger.warn(`ocmemog continuity ingest scheduling failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  });

  // Safety default (2026-03-18): auto prompt hydration is opt-in.
  // Rationale: continuity wrappers can contribute to prompt bloat/context-window
  // failures if a host runtime persists prepended context into transcript history.
  // Keep the memory backend and sidecar tools active, but only prepend continuity
  // when explicitly enabled and after the host runtime has been validated.
  if (AUTO_HYDRATION_ENABLED) {
    api.on("before_prompt_build", async (event, ctx) => {
      try {
        const scope = resolveHydrationScope(event.messages ?? [], ctx);
        if (!scope.session_id && !scope.thread_id && !scope.conversation_id) {
          return;
        }
        const payload = await postJson<ConversationHydrateResponse>(config, "/conversation/hydrate", {
          ...scope,
          turns_limit: 4,
          memory_limit: 3,
        });
        const prependContext = buildHydrationContext(payload);
        if (!prependContext) {
          return;
        }
        return { prependContext };
      } catch (error) {
        api.logger.warn(`ocmemog answer hydration failed: ${error instanceof Error ? error.message : String(error)}`);
        return;
      }
    });
  } else {
    api.logger.info("ocmemog auto prompt hydration disabled (set OCMEMOG_AUTO_HYDRATION=true to re-enable after validating host prompt behavior)");
  }

  api.on("after_compaction", async (_event, ctx) => {
    try {
      const sessionId = firstString(ctx.sessionKey, ctx.sessionId);
      if (!sessionId) {
        return;
      }
      await postJson<ConversationCheckpointResponse>(config, "/conversation/checkpoint", {
        session_id: sessionId,
        checkpoint_kind: "compaction",
        turns_limit: 32,
      });
    } catch (error) {
      api.logger.warn(`ocmemog compaction checkpoint failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  });

  api.on("session_end", async (_event, ctx) => {
    try {
      const sessionId = firstString(ctx.sessionKey, ctx.sessionId);
      if (!sessionId) {
        return;
      }
      await postJson<ConversationCheckpointResponse>(config, "/conversation/checkpoint", {
        session_id: sessionId,
        checkpoint_kind: "session_end",
        turns_limit: 48,
      });
    } catch (error) {
      api.logger.warn(`ocmemog session-end checkpoint failed: ${error instanceof Error ? error.message : String(error)}`);
    }
  });
}

const ocmemogPlugin = {
  id: "memory-ocmemog",
  name: "Memory (OCMemog)",
  description: "OC memory plugin backed by the brAIn-derived ocmemog engine.",
  kind: "memory",
  register(api: OpenClawPluginApi) {
    const config = readConfig(api.pluginConfig);

    registerAutomaticContinuityHooks(api, config);

    api.registerTool(
      {
        name: "memory_search",
        label: "Memory Search",
        description: "Search the ocmemog sidecar for stored long-term memories.",
        parameters: {
          type: "object",
          additionalProperties: false,
          required: ["query"],
          properties: {
            query: { type: "string", description: "Search query." },
            limit: { type: "number", description: "Maximum results to return." },
            categories: {
              type: "array",
              items: { type: "string" },
              description: "Memory category.",
            },
          },
        },
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          try {
            const payload = await postJson<SearchResponse>(config, "/memory/search", {
              query: params.query,
              limit: params.limit,
              categories: params.categories,
            });

            const results = payload.results ?? [];
            const text =
              results.length > 0
                ? results
                    .map((item, index) => {
                      const score = typeof item.score === "number" ? ` (${item.score.toFixed(3)})` : "";
                      return `${index + 1}. ${item.reference}${score}\n${String(item.content ?? "").slice(0, 280)}`;
                    })
                    .join("\n\n")
                : payload.error || "No memories found.";

            return {
              content: [{ type: "text", text: `${text}${formatWarnings(payload)}` }],
              details: payload,
            };
          } catch (error) {
            const message =
              error instanceof Error ? error.message : "unknown sidecar failure";
            return {
              content: [
                {
                  type: "text",
                  text:
                    `ocmemog sidecar request failed for memory_search.\n` +
                    `endpoint: ${config.endpoint}\n` +
                    `error: ${message}\n` +
                    `TODO: start the FastAPI sidecar before using this tool.`,
                },
              ],
              details: { ok: false, endpoint: config.endpoint, error: message },
            };
          }
        },
      },
      { name: "memory_search" },
    );

    api.registerTool(
      {
        name: "memory_get",
        label: "Memory Get",
        description: "Fetch a memory record by the reference returned from memory_search.",
        parameters: {
          type: "object",
          additionalProperties: false,
          required: ["reference"],
          properties: {
            reference: { type: "string", description: "Memory reference, for example knowledge:12." },
          },
        },
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          try {
            const payload = await postJson<GetResponse>(config, "/memory/get", {
              reference: params.reference,
            });

            const text = payload.ok
              ? JSON.stringify(payload.memory ?? {}, null, 2)
              : payload.error || "Memory lookup failed.";

            return {
              content: [{ type: "text", text: `${text}${formatWarnings(payload)}` }],
              details: payload,
            };
          } catch (error) {
            const message =
              error instanceof Error ? error.message : "unknown sidecar failure";
            return {
              content: [
                {
                  type: "text",
                  text:
                    `ocmemog sidecar request failed for memory_get.\n` +
                    `endpoint: ${config.endpoint}\n` +
                    `error: ${message}\n` +
                    `TODO: start the FastAPI sidecar before using this tool.`,
                },
              ],
              details: { ok: false, endpoint: config.endpoint, error: message },
            };
          }
        },
      },
      { name: "memory_get" },
    );

    api.registerTool(
      {
        name: "memory_recent",
        label: "Memory Recent",
        description: "Fetch recent memories from ocmemog by category and time window.",
        parameters: {
          type: "object",
          additionalProperties: false,
          properties: {
            categories: {
              type: "array",
              items: { type: "string" },
              description: "Filter by memory categories.",
            },
            limit: { type: "number", description: "Maximum items per category." },
            hours: { type: "number", description: "Lookback window in hours." },
          },
        },
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          try {
            const payload = await postJson<RecentResponse>(config, "/memory/recent", {
              categories: params.categories,
              limit: params.limit,
              hours: params.hours,
            });

            const results = payload.results ?? {};
            const text = Object.keys(results).length
              ? Object.entries(results)
                  .map(([category, items]) => {
                    const lines = (items || []).map((item, index) =>
                      `${index + 1}. ${item.reference}${item.timestamp ? ` (${item.timestamp})` : ""}\n${String(item.content ?? "").slice(0, 240)}`,
                    );
                    return `## ${category}\n${lines.join("\n\n")}`;
                  })
                  .join("\n\n")
              : payload.error || "No recent memories found.";

            return {
              content: [{ type: "text", text: `${text}${formatWarnings(payload)}` }],
              details: payload,
            };
          } catch (error) {
            const message =
              error instanceof Error ? error.message : "unknown sidecar failure";
            return {
              content: [
                {
                  type: "text",
                  text:
                    `ocmemog sidecar request failed for memory_recent.\n` +
                    `endpoint: ${config.endpoint}\n` +
                    `error: ${message}\n` +
                    `TODO: start the FastAPI sidecar before using this tool.`,
                },
              ],
              details: { ok: false, endpoint: config.endpoint, error: message },
            };
          }
        },
      },
      { name: "memory_recent" },
    );

    api.registerTool(
      {
        name: "memory_ingest",
        label: "Memory Ingest",
        description: "Ingest raw content into ocmemog as an experience or memory record.",
        parameters: {
          type: "object",
          additionalProperties: false,
          required: ["content"],
          properties: {
            content: { type: "string", description: "Raw content to ingest." },
            kind: { type: "string", description: "experience or memory" },
            memoryType: { type: "string", description: "memory bucket (knowledge/reflections/etc.)" },
            source: { type: "string", description: "Optional source label." },
            taskId: { type: "string", description: "Optional task id for experience ingest." },
          },
        },
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          try {
            const payload = await postJson<{ ok: boolean }>(config, "/memory/ingest", {
              content: params.content,
              kind: params.kind,
              memory_type: params.memoryType,
              source: params.source,
              task_id: params.taskId,
            });
            return {
              content: [{ type: "text", text: `memory_ingest: ${payload.ok ? "ok" : "failed"}` }],
              details: payload,
            };
          } catch (error) {
            const message = error instanceof Error ? error.message : "unknown sidecar failure";
            return {
              content: [
                {
                  type: "text",
                  text:
                    `ocmemog sidecar request failed for memory_ingest.\n` +
                    `endpoint: ${config.endpoint}\n` +
                    `error: ${message}\n` +
                    `TODO: start the FastAPI sidecar before using this tool.`,
                },
              ],
              details: { ok: false, endpoint: config.endpoint, error: message },
            };
          }
        },
      },
      { name: "memory_ingest" },
    );

    api.registerTool(
      {
        name: "memory_distill",
        label: "Memory Distill",
        description: "Run a distillation pass on recent experiences in ocmemog.",
        parameters: {
          type: "object",
          additionalProperties: false,
          properties: {
            limit: { type: "number", description: "Max experiences to distill." },
          },
        },
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          try {
            const payload = await postJson<{ ok: boolean; count?: number }>(config, "/memory/distill", {
              limit: params.limit,
            });
            return {
              content: [
                { type: "text", text: `memory_distill: ${payload.ok ? "ok" : "failed"} (${payload.count ?? 0})` },
              ],
              details: payload,
            };
          } catch (error) {
            const message = error instanceof Error ? error.message : "unknown sidecar failure";
            return {
              content: [
                {
                  type: "text",
                  text:
                    `ocmemog sidecar request failed for memory_distill.\n` +
                    `endpoint: ${config.endpoint}\n` +
                    `error: ${message}\n` +
                    `TODO: start the FastAPI sidecar before using this tool.`,
                },
              ],
              details: { ok: false, endpoint: config.endpoint, error: message },
            };
          }
        },
      },
      { name: "memory_distill" },
    );
  },
};

export default ocmemogPlugin;
