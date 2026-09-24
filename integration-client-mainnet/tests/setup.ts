// Nenhum teste pode acessar a rede: fetch global bloqueado.
// (Os testes injetam fetch/Pera/schemes falsos.)
globalThis.fetch = (async (input: unknown) => {
  throw new Error(`network access is forbidden in tests: ${String(input)}`);
}) as typeof fetch;
