// Claude-native slash commands surfaced in the composer's `/` picker.
// Selecting one creates the same chip as a skill; ChatInput already sends
// `/${name} ${text}` so these pass straight through the runner to the CLI,
// which executes built-in commands natively in SDK/stream-json mode.
// Keep this list to commands verified to work headless (see /compact,
// /context probes) — interactive-only commands just print a usage line.
export const NATIVE_COMMANDS = [
  { name: 'compact', level: 'builtin', descriptionKey: 'skillPicker.native.compact' },
  { name: 'context', level: 'builtin', descriptionKey: 'skillPicker.native.context' },
]
