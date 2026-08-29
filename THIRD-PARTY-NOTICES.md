# Third-Party Notices

Nexa Assistant bundles or downloads the following third-party components.
Each remains under its own original license; none of them are covered
by this repository's [GPL-3.0](LICENSE) or [proprietary notice](LICENSE-PRIVATE.md).

## openWakeWord shared models

- `data/wakeword-models/embedding_model.onnx`
- `data/wakeword-models/melspectrogram.onnx`

Source: https://huggingface.co/davidscripka/openwakeword
License: CC BY-NC-SA 4.0 (Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International)

These are the shared feature-extraction models used by the openWakeWord
library. Nexa's own custom wake-word model, `hey_nexa.onnx`, is trained
on top of these and is covered separately by [LICENSE-PRIVATE.md](LICENSE-PRIVATE.md).

## Whisper speech-to-text model

Downloaded at build time from:
https://huggingface.co/ggerganov/whisper.cpp

License: MIT

## Piper text-to-speech voices (Amy, Ryan)

Downloaded at build time from:
https://huggingface.co/rhasspy/piper-voices

License: MIT

## Piper TTS engine binary

Downloaded at build time from:
https://github.com/rhasspy/piper

License: MIT

## whisper.cpp

Downloaded at build time from:
https://github.com/ggerganov/whisper.cpp

License: MIT

## openWakeWord (library) and its Python dependencies

Downloaded at build time from PyPI. See each package's own license
(openWakeWord, onnxruntime, scikit-learn, scipy, numpy, and others).
