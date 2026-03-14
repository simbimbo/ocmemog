import { Type } from "@sinclair/typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";

const DEFAULT_ENDPOINT = "http://127.0.0.1:17890";
const DEFAULT_TIMEOUT_MS = 10_000;

type PluginConfig = {
  endpoint: string;
  timeoutMs: number;
};

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

function readConfig(raw: unknown): PluginConfig {
  const cfg = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return {
    endpoint: typeof cfg.endpoint === "string" && cfg.endpoint.trim() ? cfg.endpoint.trim() : DEFAULT_ENDPOINT,
    timeoutMs:
      typeof cfg.timeoutMs === "number" && Number.isFinite(cfg.timeoutMs) && cfg.timeoutMs > 0
        ? cfg.timeoutMs
        : DEFAULT_TIMEOUT_MS,
  };
}

async function postJson<T>(config: PluginConfig, path: string, body: Record<string, unknown>): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const response = await fetch(new URL(path, config.endpoint).toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
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

const ocmemogPlugin = {
  id: "memory-ocmemog",
  name: "Memory (OCMemog)",
  description: "OC memory plugin backed by the brAIn-derived ocmemog engine.",
  kind: "memory",
  register(api: OpenClawPluginApi) {
    const config = readConfig(api.pluginConfig);

    api.registerTool(
      {
        name: "memory_search",
        label: "Memory Search",
        description: "Search the ocmemog sidecar for stored long-term memories.",
        parameters: Type.Object({
          query: Type.String({ description: "Search query." }),
          limit: Type.Optional(Type.Number({ description: "Maximum results to return." })),
          categories: Type.Optional(Type.Array(Type.String({ description: "Memory category." }))),
        }),
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
        parameters: Type.Object({
          reference: Type.String({ description: "Memory reference, for example knowledge:12." }),
        }),
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
        name: "memory_ingest",
        label: "Memory Ingest",
        description: "Ingest raw content into ocmemog as an experience or memory record.",
        parameters: Type.Object({
          content: Type.String({ description: "Raw content to ingest." }),
          kind: Type.Optional(Type.String({ description: "experience or memory" })),
          memoryType: Type.Optional(Type.String({ description: "memory bucket (knowledge/reflections/etc.)" })),
          source: Type.Optional(Type.String({ description: "Optional source label." })),
          taskId: Type.Optional(Type.String({ description: "Optional task id for experience ingest." })),
        }),
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
        parameters: Type.Object({
          limit: Type.Optional(Type.Number({ description: "Max experiences to distill." })),
        }),
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
