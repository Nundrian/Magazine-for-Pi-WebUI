import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

export async function executionCatalogue(models, SettingsManager, native = {}, version, sdkDir) {
  if (version !== '1.5.2') {
    // Earlier integrations keep their established execution behaviour.
    return models.map(model => ({id:`${model.provider}/${model.id}`, name:model.name?.trim() || model.id,
      provider:model.provider, thinking_levels:['off']}));
  }
  const require = createRequire(path.join(sdkDir, 'package.json'));
  const ai = require.resolve.paths('@earendil-works/pi-ai')
    .map(dir => path.join(dir, '@earendil-works/pi-ai', 'package.json')).find(file => fs.existsSync(file));
  if (!ai) throw new Error('Could not resolve the Pi SDK model capabilities');
  const modelsModule = JSON.parse(fs.readFileSync(ai, 'utf8')).exports['./models'].import;
  const {getSupportedThinkingLevels, clampThinkingLevel} = await import(pathToFileURL(path.join(path.dirname(ai), modelsModule)));
  const {DEFAULT_THINKING_LEVEL} = await import(pathToFileURL(path.join(sdkDir, 'dist/core/defaults.js')));
  const settings = SettingsManager.inMemory(native);

  return models.map(model => {
    const id = `${model.provider}/${model.id}`;
    const perModel = settings.getModelThinkingLevel(model.provider, model.id);
    const global = settings.getDefaultThinkingLevel();
    const inherited = clampThinkingLevel(model, perModel ?? global ?? DEFAULT_THINKING_LEVEL);
    return {id, name:model.name?.trim() ? model.name : model.id, provider:model.provider,
      thinking_levels:getSupportedThinkingLevels(model), inherited_thinking_level:inherited,
      inheritance_source:perModel ? 'pi_model' : global ? 'pi_global' : 'pi_default',
      thinking_level_map:model.reasoning ? model.thinkingLevelMap ?? {} : {}, compaction:settings.getCompactionSettings(model),
      context_window:model.contextWindow, max_output:model.maxTokens, sdk_version:version};
  });
}

export function workerSettings(model, saved, version) {
  if (!saved) return {thinkingLevel:'off', compaction:{enabled:false}};
  const mapping = model.reasoning ? model.thinkingLevelMap?.[saved.effective_thinking_level] ?? null : null;
  if (saved.model !== `${model.provider}/${model.id}` || saved.sdk_version !== version ||
      saved.context_window !== model.contextWindow || saved.max_output !== model.maxTokens ||
      saved.provider_mapping !== mapping) {
    throw new Error('Pi model metadata changed after preparation; prepare a new run');
  }
  return {thinkingLevel:saved.effective_thinking_level, compaction:saved.compaction};
}
