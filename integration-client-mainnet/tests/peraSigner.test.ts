import algosdk from "algosdk";
import { describe, expect, it } from "vitest";

import { EXPECTED } from "../src/config";
import { createPeraSigner } from "../src/peraSigner";
import { FEE_PAYER, FakePera, GUARD, PAYER, buildGroup, type TxnOverrides } from "./fixtures";

describe("adaptador Pera -> ClientAvmSigner", () => {
  it("envia o grupo à Pera: só o axfer do payer é assinado; fee payer com signers []", async () => {
    const pera = new FakePera();
    let opened = 0;
    const signer = createPeraSigner(pera, PAYER, GUARD, () => opened++);
    const { encoded, txns } = buildGroup();

    const out = await signer.signTransactions(encoded, [1]);

    expect(opened).toBe(1);
    expect(pera.calls).toHaveLength(1);
    const group = pera.calls[0]![0]!;
    expect(group).toHaveLength(2);
    expect(group[0]!.signers).toEqual([]);
    expect(group[0]!.txn.sender.toString()).toBe(FEE_PAYER);
    expect(group[1]!.signers).toEqual([PAYER]);
    expect(out[0]).toBeNull();
    expect(algosdk.decodeSignedTransaction(out[1]!).txn.txID()).toBe(txns[1]!.txID());
  });

  const bad: [string, TxnOverrides][] = [
    ["amount divergente", { amount: 50001n }],
    ["ASA divergente", { assetIndex: 10458941n }],
    ["receiver divergente", { receiver: FEE_PAYER }],
    ["Testnet", { testnet: true }],
    ["rekeyTo", { rekeyTo: FEE_PAYER }],
    ["closeRemainderTo", { closeRemainderTo: FEE_PAYER }],
    ["clawback", { assetSender: FEE_PAYER }],
  ];
  for (const [name, over] of bad) {
    it(`recusa ANTES de abrir a Pera: ${name}`, async () => {
      const pera = new FakePera();
      const signer = createPeraSigner(pera, PAYER, GUARD);
      await expect(signer.signTransactions(buildGroup(over).encoded, [1])).rejects.toThrow();
      expect(pera.calls).toHaveLength(0);
    });
  }

  it("recusa assinar transação cujo sender não é a carteira conectada", async () => {
    const pera = new FakePera();
    const signer = createPeraSigner(pera, PAYER, GUARD);
    const { encoded } = buildGroup({ sender: FEE_PAYER });
    await expect(signer.signTransactions(encoded, [1])).rejects.toThrow(/sender/);
    expect(pera.calls).toHaveLength(0);
  });

  it("recusa pedidos para assinar mais de uma transação (ex.: a do fee payer)", async () => {
    const pera = new FakePera();
    const signer = createPeraSigner(pera, PAYER, GUARD);
    await expect(signer.signTransactions(buildGroup().encoded, [0, 1])).rejects.toThrow();
    await expect(createPeraSigner(pera, PAYER, GUARD).signTransactions(buildGroup().encoded)).rejects.toThrow();
    expect(pera.calls).toHaveLength(0);
  });

  it("recusa grupo com transação adicional", async () => {
    const pera = new FakePera();
    const signer = createPeraSigner(pera, PAYER, GUARD);
    const { encoded } = buildGroup();
    await expect(signer.signTransactions([...encoded, encoded[0]!], [1])).rejects.toThrow(/Grupo x402 inesperado/);
    expect(pera.calls).toHaveLength(0);
  });

  it("signer é de uso único: segunda chamada nunca abre a Pera", async () => {
    const pera = new FakePera();
    const signer = createPeraSigner(pera, PAYER, GUARD);
    await signer.signTransactions(buildGroup().encoded, [1]);
    await expect(signer.signTransactions(buildGroup().encoded, [1])).rejects.toThrow(/já foi usado/);
    expect(pera.calls).toHaveLength(1);
  });

  it("usuário rejeita na Pera -> erro propagado, sem nova tentativa", async () => {
    const pera = new FakePera("reject");
    const signer = createPeraSigner(pera, PAYER, GUARD);
    await expect(signer.signTransactions(buildGroup().encoded, [1])).rejects.toThrow(/rejected/);
    expect(pera.calls).toHaveLength(1);
  });

  it("detecta transação assinada diferente da solicitada", async () => {
    const signer = createPeraSigner(new FakePera("tamper"), PAYER, GUARD);
    await expect(signer.signTransactions(buildGroup().encoded, [1])).rejects.toThrow(/difere/);
  });

  it("detecta número inesperado de assinaturas", async () => {
    const signer = createPeraSigner(new FakePera("extra"), PAYER, GUARD);
    await expect(signer.signTransactions(buildGroup().encoded, [1])).rejects.toThrow(/assinaturas/);
  });

  it("rejeita endereço inválido", () => {
    expect(() => createPeraSigner(new FakePera(), "not-an-address", GUARD)).toThrow();
  });

  it("sanidade: o grupo de fixture é Mainnet", () => {
    const { txns } = buildGroup();
    expect(txns[1]!.genesisID).toBe(EXPECTED.genesisId);
  });
});
