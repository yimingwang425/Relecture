<!--
自写合成内容，非第三方课件。**复现材料，不进 pairs.json。**

用途：发现 A（P3 在 `synthetic_deck.md` 上 11 次 0 达标）的**单变量复现**。
只换课件这一个变量——提问模板、模型、effort、thinking、max_tokens、
StyleConfig(P3) 全部与主实验一致。

作者：本项目（2026-09-09）。主题：Nyquist 采样与混叠（信号处理）。
与 `synthetic_deck.md`（Bloom filter / 数据结构）不同学科，
也不撞 22.081J（能源/核工程）与 14.129（博弈论）。

## 按第一份的同一份规格写

这不是「另找一个样本」，是把课件当自变量。若第二份是照着「让 P3 好达标」去写的
（比如写得更短、层次更少），那测的就是「短课件更容易讲短」——一个平凡结论。
所以四条轴的着力点逐条对齐第一份：

| 轴 | 第一份（Bloom filter） | 第二份（Nyquist） |
| --- | --- | --- |
| 详细程度 | 六层：问题→结构→两个操作→无假阴性→误判率→参数选择 | 六层：问题→采样→混叠机制→为什么不可逆→阈值推导→实践取值 |
| 深度 | 四步可推的误判率式子，独立性假设显式 | 四步可推的 `f_s > 2f_max`，带限假设显式 |
| 结构 | 可前可后的结论「换空间、永不漏报」 | 可前可后的结论「过阈值则可完全重建，否则信息不可逆丢失」 |
| 举例 | 共用挂钩的衣帽间 + 浏览器/存储引擎 | 车轮倒转 + 音频 44.1 kHz |

词数也对齐：第一份 503 词，本份 504 词。
-->

# Lecture 4 — Sampling and Aliasing

**EE 1xx · Signals and Systems**

## The problem

A microphone produces a continuous voltage. A computer can only store numbers. So
at some point the signal must be measured at discrete instants — sampled — and
everything between two measurements is discarded. The question this lecture answers
is when that discarding is harmless and when it destroys the signal for good.

## Sampling

Sampling means recording the value of a signal every `T` seconds. The sampling
rate is `f_s = 1/T`. The recorded sequence is all the receiver ever sees; any claim
about reconstructing the original waveform has to be justified from that sequence
alone.

## What goes wrong: aliasing

Consider a pure sine wave at frequency `f`, sampled at rate `f_s`. Now consider a
second sine wave at frequency `f + f_s`. At every sampling instant `t = nT`, the two
waves have exactly the same value, because the extra term contributes a whole number
of cycles between samples and lands back where it started.

The consequence is severe. The two waves are different signals, but they produce an
identical sample sequence. Once you hold only the samples, no algorithm can tell
them apart, because the information that separated them was never recorded. A high
frequency has been disguised as a low one. This is *aliasing*.

## Why it cannot be undone afterwards

It is tempting to look for a clever filter that removes aliases after sampling. No
such filter exists, and the reason is not that nobody has found one. Two distinct
inputs were mapped to one identical output; the map is not injective, so it has no
inverse. Aliasing must be prevented before sampling — by filtering the continuous
signal — or not at all.

## The threshold

Assume the signal is *band-limited*: it contains no energy above some frequency
`f_max`. Then:

1. Every component sits somewhere in the band `-f_max` to `f_max`.
2. Sampling at rate `f_s` makes each component indistinguishable from copies of
   itself shifted by whole multiples of `f_s`.
3. Those shifted copies stay clear of the original band as long as `f_s - f_max`
   is greater than `f_max`.
4. Rearranging gives `f_s > 2f_max`. Above that rate the original band is recovered
   without ambiguity; at or below it, copies overlap and the overlap is permanent.

The band-limited assumption in step 1 is doing real work: no physical signal is
perfectly band-limited, which is why real systems apply an analog low-pass filter
before the sampler rather than trusting the source.

## In practice

A wagon wheel filmed at 24 frames per second appears to rotate backwards once it
spins fast enough — the camera is a sampler, and the wheel is aliased. Audio is
sampled at 44.1 kHz because human hearing stops near 20 kHz, leaving margin for a
filter that cannot cut off infinitely sharply.

## The limitation

The theorem promises exact reconstruction only for perfectly band-limited signals
observed for all time. Finite recordings and imperfect filters mean the guarantee
is an idealisation that practice approaches but never reaches.
