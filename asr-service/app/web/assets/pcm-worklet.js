/* AudioWorklet 处理器（实时页麦克风采集）：在独立音频线程缓冲 Float32 帧，
 * 累积 2048 样本后 postMessage（transferable）回主线程，由主线程重采样到 16k 并经 WS 发送。
 * worklet 不写 output（输出静音），连接 destination 仅为驱动音频图。
 */
class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._count = 0;
    this._target = 2048;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) {
      this._buf.push(new Float32Array(ch));
      this._count += ch.length;
      if (this._count >= this._target) {
        const merged = new Float32Array(this._count);
        let offset = 0;
        for (const b of this._buf) {
          merged.set(b, offset);
          offset += b.length;
        }
        this.port.postMessage(merged, [merged.buffer]);
        this._buf = [];
        this._count = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-worklet', PCMWorklet);
