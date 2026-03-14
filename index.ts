import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";

const ocmemogPlugin = {
  id: "memory-ocmemog",
  name: "Memory (OCMemog)",
  description: "OC memory plugin backed by the brAIn-derived ocmemog engine.",
  kind: "memory",
  register(_api: OpenClawPluginApi) {
    // TODO: wire OpenClaw memory tools to the ocmemog engine.
    // This will likely register memory_search + memory_get via a sidecar/adapter.
  },
};

export default ocmemogPlugin;
