#!/usr/bin/env python3
"""Convert a Whisper checkpoint and generate the fast tokenizer it needs."""
import argparse
from pathlib import Path


def main():
    from ctranslate2.converters import TransformersConverter
    from transformers import WhisperTokenizerFast

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', help='Hugging Face model ID or local checkpoint')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    # Some Persian checkpoints have vocab.json/merges.txt, but no tokenizer.json.
    tokenizer = WhisperTokenizerFast.from_pretrained(args.model)
    converter = TransformersConverter(args.model, copy_files=['preprocessor_config.json'])
    converter.convert(str(args.output), quantization='int8', force=True)
    tokenizer.save_pretrained(str(args.output))
    if not (args.output / 'tokenizer.json').is_file():
        raise RuntimeError('Conversion did not produce tokenizer.json')


if __name__ == '__main__':
    main()
