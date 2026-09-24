import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const SRC = join(ROOT, "src");
const srcFiles = readdirSync(SRC).filter((f) => f.endsWith(".ts"));
const read = (p: string) => readFileSync(p, "utf-8");

describe("ausência de chaves / mnemonic / storage", () => {
  const forbidden =
    /mnemonic|privateKey|private_key|secretKey|secret_key|\bsk\b|toClientAvmSigner|toFacilitatorAvmSigner|generateAccount|mnemonicToSecretKey|secretKeyToMnemonic|seedFromMnemonic|localStorage|sessionStorage|indexedDB|import\.meta\.env/i;

  for (const f of srcFiles) {
    it(`src/${f} não manipula chaves nem browser storage`, () => {
      expect(read(join(SRC, f))).not.toMatch(forbidden);
    });
  }

  it("index.html não tem campo de entrada para segredo", () => {
    const html = read(join(ROOT, "index.html"));
    expect(html.match(/<input/g)).toHaveLength(1);
    expect(html).toMatch(/<input id="mint"/);
    expect(html).not.toMatch(/type="password"|<textarea/i);
  });

  it("nenhum .env no cliente", () => {
    expect(readdirSync(ROOT).filter((f) => f.startsWith(".env"))).toEqual([]);
  });

  it("dependências não incluem gerenciamento de chaves", () => {
    const pkg = JSON.parse(read(join(ROOT, "package.json")));
    expect(Object.keys(pkg.dependencies).sort()).toEqual(
      ["@algorandfoundation/algokit-utils", "@perawallet/connect", "@x402/avm", "@x402/core", "algosdk"].sort(),
    );
  });
});

describe("pagamento só com ação explícita / somente Testnet", () => {
  const main = read(join(SRC, "main.ts"));

  it("Pera travada em TestNet (chainId 416002)", () => {
    expect(read(join(SRC, "config.ts"))).toMatch(/PERA_TESTNET_CHAIN_ID = 416002/);
    expect(main).toMatch(/new PeraWalletConnect\(\{ chainId: PERA_TESTNET_CHAIN_ID/);
    expect(main).not.toMatch(/416001/);
  });

  it("botão PAY 0.05 USDC TESTNET existe e começa desabilitado", () => {
    const html = read(join(ROOT, "index.html"));
    expect(html).toMatch(/<button id="pay" disabled>PAY 0\.05 USDC TESTNET<\/button>/);
    expect(read(join(SRC, "config.ts"))).toMatch(/PAY_BUTTON_LABEL = "PAY 0\.05 USDC TESTNET"/);
  });

  it("pay() só é chamado dentro do handler de clique confiável, com trava de página", () => {
    const calls = main.match(/\.pay\(/g) ?? [];
    expect(calls).toHaveLength(1);
    const handler = main
      .slice(main.indexOf('ui.pay.addEventListener("click"'))
      .replace(/\/\/.*$/gm, ""); // ignora comentários
    expect(handler).toMatch(/!ev\.isTrusted \|\| paymentAttempted/);
    const lock = handler.indexOf("paymentAttempted = true");
    expect(lock).toBeGreaterThan(0);
    expect(lock).toBeLessThan(handler.search(/\bawait\s/));
  });

  it("nenhum retry automático / timers", () => {
    for (const f of srcFiles) {
      expect(read(join(SRC, f))).not.toMatch(/setTimeout|setInterval|retry\(/);
    }
  });
});
