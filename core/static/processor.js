// app-gateway/static/processor.js

class AudioProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        // 250ms chunks at 16kHz = 4000 samples
        this.chunkSize = 4000;
        this.buffer = new Float32Array(this.chunkSize);
        this.bytesWritten = 0;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || input.length === 0) return true;

        const inputChannel = input[0];

        for (let i = 0; i < inputChannel.length; i++) {
            this.buffer[this.bytesWritten++] = inputChannel[i];

            if (this.bytesWritten >= this.chunkSize) {
                // Send copy of buffer to avoid race conditions
                this.port.postMessage(this.buffer.slice(0, this.chunkSize));
                this.bytesWritten = 0;
            }
        }

        return true;
    }
}

registerProcessor('audio-processor', AudioProcessor);
