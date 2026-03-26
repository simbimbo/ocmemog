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
  assert.equal(mod.getAutoHydrationDecision('chat-local').reason, 'denied_by_agent_id');

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
  assert.equal(mod.getAutoHydrationDecision('main').reason, 'allowed_by_allowlist');
  assert.equal(mod.getAutoHydrationDecision('chat-local').reason, 'not_in_allowlist');
  assert.equal(mod.getAutoHydrationDecision(undefined).reason, 'not_in_allowlist');

  delete process.env.OCMEMOG_AUTO_HYDRATION;
  delete process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS;
  delete process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS;
});

test('auto hydration decision reports global disable explicitly', async () => {
  process.env.OCMEMOG_AUTO_HYDRATION = 'false';
  delete process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS;
  delete process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS;

  const mod = await loadPluginModule();
  const decision = mod.getAutoHydrationDecision('main');
  assert.equal(decision.allowed, false);
  assert.equal(decision.reason, 'disabled_globally');

  delete process.env.OCMEMOG_AUTO_HYDRATION;
});

test('auto hydration log formatter includes decision context', async () => {
  process.env.OCMEMOG_AUTO_HYDRATION = 'true';
  process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS = 'main';
  process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS = 'chat-local';

  const mod = await loadPluginModule();
  const allowedLog = mod.formatAutoHydrationDecisionLog(mod.getAutoHydrationDecision('main'));
  const deniedLog = mod.formatAutoHydrationDecisionLog(mod.getAutoHydrationDecision('chat-local'));
  assert.match(allowedLog, /agent=main/);
  assert.match(allowedLog, /reason=allowed_by_allowlist/);
  assert.match(deniedLog, /agent=chat-local/);
  assert.match(deniedLog, /reason=denied_by_agent_id/);

  delete process.env.OCMEMOG_AUTO_HYDRATION;
  delete process.env.OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS;
  delete process.env.OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS;
});
