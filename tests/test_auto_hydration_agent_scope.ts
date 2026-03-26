import test from 'node:test';
import assert from 'node:assert/strict';

async function loadPluginModule() {
  return await import('../index.ts');
}

test('auto hydration agent scope denylist overrides global enable', async () => {
  process.env.OCMEMOG_AUTO_HYDRATION = 'true';
  process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS = 'chat-local';
  process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS = '';

  const mod = await loadPluginModule();
  assert.equal(mod.shouldAutoHydrateForAgent('chat-local'), false);
  assert.equal(mod.shouldAutoHydrateForAgent('main'), true);

  delete process.env.OCMEMOG_AUTO_HYDRATION;
  delete process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS;
  delete process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS;
});

test('auto hydration agent scope allowlist restricts before_prompt_build hydration', async () => {
  process.env.OCMEMOG_AUTO_HYDRATION = 'true';
  process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS = 'main,worker';
  process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS = '';

  const mod = await loadPluginModule();
  assert.equal(mod.shouldAutoHydrateForAgent('main'), true);
  assert.equal(mod.shouldAutoHydrateForAgent('worker'), true);
  assert.equal(mod.shouldAutoHydrateForAgent('chat-local'), false);
  assert.equal(mod.shouldAutoHydrateForAgent(undefined), false);

  delete process.env.OCMEMOG_AUTO_HYDRATION;
  delete process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS;
  delete process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS;
});
