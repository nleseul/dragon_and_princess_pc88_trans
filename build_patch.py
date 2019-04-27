import argparse
import csv
import io
import os
import sys
from enum import Enum, auto

class InstructionsParseState(Enum):
    OUTSIDE_QUOTES = auto()
    OPENING_QUOTE = auto()
    IN_QUOTES = auto()
    CLOSING_QUOTE = auto()

class DataTableParseState(Enum):
    NUMERIC_ENTRY = auto()
    VALID_ENTRY = auto()
    IN_BINARY = auto()
    ENDING_BINARY = auto()


def import_csv(filename):
    lookup = {}
    try:
        with open(filename, encoding='utf8') as in_file:
            reader = csv.reader(in_file, lineterminator='\n')

            for row in reader:
                lookup[row[0]] = row[1:]
    except FileNotFoundError:
        pass

    return lookup

def write_csv(filename, bounds_list, lookup):
    with open(filename, 'w+', encoding='utf8') as out_file:
        writer = csv.writer(out_file, lineterminator='\n')

        for bounds in bounds_list:
            try:
                text = buf[bounds[0]:bounds[1]].decode('shift_jis')
                row = [text]
                if text in lookup:
                    row += lookup[text]
                writer.writerow(row)
            except UnicodeDecodeError:
                pass

def patch_in_translations(buf, bounds_list, lookup):
    for bounds in bounds_list:
        try:
            original_text = buf[bounds[0]:bounds[1]]
            decoded_text = original_text.decode('shift_jis')
            translated_text = lookup[decoded_text][0]

            if len(translated_text) > 0:
                encoded_translated_text = translated_text.encode('shift_jis')
                if len(encoded_translated_text) > len(original_text):
                    print("Warning! Text '{0}' is too long for the available space of {1} bytes.".format(translated_text, len(original_text)))
                    encoded_translated_text = encoded_translated_text[:len(original_text)]
                encoded_translated_text = encoded_translated_text.ljust(len(original_text), b'\x20')

                print("{0} -> {1}".format(original_text, encoded_translated_text))

                buf[bounds[0]:bounds[1]] = encoded_translated_text

        except (UnicodeDecodeError, KeyError, IndexError):
            pass

if __name__ == '__main__':

    parser = argparse.ArgumentParser('Main patch build for Dragon & Princess')
    parser.add_argument('in_disk_image', help='Disk image to scan for original text.')
    parser.add_argument('out_disk_image', help='Disk image to patch (WILL be modified!).')

    parser.add_argument('--update_csv', help='Whether the CSV files should be created/updated with the strings found in the scan. Will not overwrite old entries.', action='store_true')

    args = parser.parse_args()

    # Load the current CSVs
    game_text_lookup = import_csv('csv/gametext.csv')
    misc_text_lookup = import_csv('csv/misctext.csv')

    # Read the sectors of the disk that matter to us.
    buf = bytearray()

    with open(args.in_disk_image, 'rb') as in_file:

        in_file.seek(0x2a230)

        while in_file.tell() < 0x31600:
            in_file.seek(0x10, os.SEEK_CUR)
            buf += in_file.read(0x100)

    parse_state = InstructionsParseState.OUTSIDE_QUOTES

    game_text_bounds = []
    misc_text_bounds = []

    current_range_begin = None

    # Scan the whole file for quoted text (begins with ‘")
    with io.BytesIO(buf) as data:
        while True:
            c = data.read(1)
            if c is None or len(c) == 0:
                break
            elif parse_state == InstructionsParseState.OUTSIDE_QUOTES:
                if c == b'\x91' or c == b'\x3b':
                    parse_state = InstructionsParseState.OPENING_QUOTE
            elif parse_state == InstructionsParseState.OPENING_QUOTE:
                if c == b'\x22':
                    parse_state = InstructionsParseState.IN_QUOTES
                    current_range_begin = data.tell()
                elif c == b'\x20':
                    pass
                else:
                    parse_state = InstructionsParseState.OUTSIDE_QUOTES
            elif parse_state == InstructionsParseState.IN_QUOTES:
                if c == b'\x22':
                    parse_state = InstructionsParseState.CLOSING_QUOTE
            elif parse_state == InstructionsParseState.CLOSING_QUOTE:
                if c == b'\x3a' or c == b'\x3b' or c == b'\x00':
                    game_text_bounds.append((current_range_begin, data.tell() - 2))
                    current_range_begin = None
                    parse_state = InstructionsParseState.OUTSIDE_QUOTES
                else:
                    parse_state = InstructionsParseState.IN_QUOTES
            else:
                raise Exception("Unknown parse state!")

    # Scan the end bits of the file for comma-separated data tables that include text
    with io.BytesIO(buf) as data:
        data.seek(0x60d5)
        parse_state = DataTableParseState.NUMERIC_ENTRY
        current_range_begin = data.tell()

        while True:
            c = data.read(1)

            if c is None or len(c) == 0:
                break
            elif parse_state == DataTableParseState.NUMERIC_ENTRY:
                if c == b'\x00':
                    parse_state = DataTableParseState.IN_BINARY
                elif c == b'\x2c':
                    current_range_begin = data.tell()
                elif not(c[0] >= ord('0') and c[0] <= ord('9')) and c != b'\x2d' and c != b'\x2e':
                    parse_state = DataTableParseState.VALID_ENTRY
            elif parse_state == DataTableParseState.VALID_ENTRY:
                if c == b'\x00' or c == b'\x2c':
                    misc_text_bounds.append((current_range_begin, data.tell() - 1))
                    current_range_begin = data.tell()
                    parse_state = (DataTableParseState.IN_BINARY if c == b'\x00' else DataTableParseState.NUMERIC_ENTRY)
            elif parse_state == DataTableParseState.IN_BINARY:
                if c == b'\x84':
                    parse_state = DataTableParseState.ENDING_BINARY
            elif parse_state == DataTableParseState.ENDING_BINARY:
                if c == b'\x20':
                    parse_state = DataTableParseState.NUMERIC_ENTRY
                    current_range_begin = data.tell()
            else:
                raise Exception("Unknown parse state!")

    # Write out updated CSVs if requested.
    if args.update_csv:
        write_csv('csv/gametext.csv', game_text_bounds, game_text_lookup)
        write_csv('csv/misctext.csv', misc_text_bounds, misc_text_lookup)

    # Write translated text into the buffer.
    patch_in_translations(buf, game_text_bounds, game_text_lookup)
    patch_in_translations(buf, misc_text_bounds, misc_text_lookup)

    # Dump the whole original disk image into the output file to start with.
    with open(args.in_disk_image, 'rb') as in_file, open(args.out_disk_image, 'w+b') as out_file:
        out_file.write(in_file.read())

    # Then overwrite the important sectors in the output file with chunks from the local buffer.
    with open(args.out_disk_image, 'r+b') as out_file, io.BytesIO(buf) as data:

        out_file.seek(0x2a230)

        while out_file.tell() < 0x31600:
            out_file.seek(0x10, os.SEEK_CUR)
            out_file.write(data.read(0x100))