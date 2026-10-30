"""Train the character language model of 長句選字加強 on the RTX 4090.

    py tools/lm/train.py <S|M|L|XL> [--minutes N] [--quick]

Reads common.WORK/train.u16, dev.u16, vocab.txt (tools/lm/prepare.py). A GRU over characters:
embedding E, GRU H x layers, a projection back to E and the embedding again as the output layer
(tied: one table, a smaller file). Each sample starts at a <s> with a zero state, as the engine
starts each run of Chinese. Keeps the checkpoint with the best development loss:
common.WORK/models/<size>.pt (+ .json: sizes, loss, characters seen).
"""
import json
import math
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from common import WORK  # noqa: E402

SIZES = {"S": (128, 256, 1), "M": (192, 384, 1), "L": (256, 512, 2),
         "XL": (384, 768, 2)}  # (embedding, hidden, layers); XL: about 11M weights, under the 15 MB the installer may grow
SEQ = 128
BATCH = 128  # (256 with a 12k-character output and three models at once filled the 24 GB: Windows then
             # spills to system memory, ten times slower)
BOS = 1


class CharLM(nn.Module):
    def __init__(self, vocab, emb, hidden, layers):
        super().__init__()
        self.emb = nn.Embedding(vocab, emb)
        self.rnn = nn.GRU(emb, hidden, num_layers=layers, batch_first=True)
        self.proj = nn.Linear(hidden, emb)
        self.bias = nn.Parameter(torch.zeros(vocab))

    def forward(self, x, h=None):
        y, h = self.rnn(self.emb(x), h)
        return self.proj(y) @ self.emb.weight.T + self.bias, h


def starts(data):
    """Positions of <s> with a whole sample after them."""
    pos = np.flatnonzero(data[:-SEQ - 1] == BOS)
    return pos


def batch(data, pos, rng, device):
    idx = rng.choice(pos, BATCH)
    x = np.stack([data[i:i + SEQ + 1] for i in idx]).astype(np.int64)
    x = torch.from_numpy(x).to(device, non_blocking=True)
    return x[:, :-1], x[:, 1:]


def loss_of(logits, y):
    # (a <s> to come is the end of a run: not something to guess)
    return nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)), y.reshape(-1), ignore_index=BOS)


def main(argv):
    size = argv[0]
    minutes = float(argv[argv.index("--minutes") + 1]) if "--minutes" in argv else 60.0
    quick = "--quick" in argv
    work = os.environ.get("DUALITYIME_LM_WORK", WORK)
    vocab = open(os.path.join(work, "vocab.txt"), encoding="utf-8").read().rstrip("\n").split("\n")
    train = np.memmap(os.path.join(work, "train.u16"), dtype=np.uint16, mode="r")
    dev = np.memmap(os.path.join(work, "dev.u16"), dtype=np.uint16, mode="r")
    train_pos, dev_pos = starts(train), starts(dev)
    rng = np.random.default_rng(1)
    dev_batches = [batch(dev, dev_pos, np.random.default_rng(100 + k), "cuda") for k in range(20)]
    emb, hidden, layers = SIZES[size]
    torch.manual_seed(1)
    model = CharLM(len(vocab), emb, hidden, layers).cuda()
    params = sum(p.numel() for p in model.parameters())
    print(f"{size}: vocab {len(vocab)}, {params / 1e6:.2f}M parameters; train {len(train)} ids, dev {len(dev)}", flush=True)
    peak = 1e-3 if hidden >= 768 else 2e-3  # (XL at 2e-3 went to nan after 22 minutes)
    opt = torch.optim.AdamW(model.parameters(), lr=peak, weight_decay=0.01)
    budget = minutes * 60
    t0 = time.time()
    step, best, seen, skipped = 0, None, 0, 0
    os.makedirs(os.path.join(work, "models"), exist_ok=True)
    out = os.path.join(work, "models", size + (".quick" if quick else ""))
    while True:
        elapsed = time.time() - t0
        if elapsed > budget:
            break
        lr = peak * 0.5 * (1 + math.cos(math.pi * min(1.0, elapsed / budget)))
        for g in opt.param_groups:
            g["lr"] = lr
        x, y = batch(train, train_pos, rng, "cuda")
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits, _ = model(x)
            loss = loss_of(logits.float(), y)
        opt.zero_grad(set_to_none=True)
        if not torch.isfinite(loss):  # (a step gone wrong is skipped, not learned from)
            skipped += 1
            continue
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        step += 1
        seen += x.numel()
        if step % 500 == 0 or time.time() - t0 > budget:
            model.eval()
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
                dl = sum(loss_of(model(dx)[0].float(), dy).item() for dx, dy in dev_batches) / len(dev_batches)
            model.train()
            print(f"step {step} {elapsed / 60:.1f} min: train {loss.item():.3f} dev {dl:.3f} "
                  f"(perplexity {math.exp(dl):.1f}), {seen / 1e9:.2f}G characters, {skipped} steps skipped", flush=True)
            if best is None or dl < best:
                best = dl
                torch.save(model.state_dict(), out + ".pt")
                with open(out + ".json", "w", encoding="utf-8") as f:
                    json.dump({"size": size, "emb": emb, "hidden": hidden, "layers": layers, "vocab": len(vocab),
                               "parameters": params, "dev_loss": dl, "step": step, "characters": seen}, f)
    print(f"best dev loss {best:.3f} (perplexity {math.exp(best):.1f}) -> {out}.pt", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
