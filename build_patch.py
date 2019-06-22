import argparse
import csv
import io
import os
import sys

def import_csv(filename):
    lookup = {}
    try:
        with open(filename, encoding='utf8') as in_file:
            reader = csv.reader(in_file, lineterminator='\n')

            for row in reader:
                if (len(row) > 2):
                    lookup[row[2]] = row[3:] if len(row) > 3 else []
    except FileNotFoundError:
        pass

    return lookup

def unpack_bytecode(data):
    lines = []

    while True:
        link_addr = int.from_bytes(data.read(2), byteorder='little')
        line_number = int.from_bytes(data.read(2), byteorder='little')

        tokens = []

        if link_addr == 0:
            break

        while True:
            c = data.read(1)

            if len(c) == 0 or c[0] == 0:
                break

            op = c[0]
            current_token = {'op': c[0]}
            force_step_back = False


            if op == 0xc or op == 0xe or op == 0x1c: # Hex constant, decimal constant, also decimal constant; 2 bytes
                current_token['content'] = data.read(2)
            elif op == 0xf: # One-byte decimal constant
                current_token['content'] = data.read(1)
            elif op == 0x1d: # Single precision float
                current_token['content'] = data.read(4)
            elif op == 0x22: # Start quote
                current_token['content'] = bytearray()
                current_token['terminator'] = op
                while True:
                    c = data.read(1)
                    if len(c) == 0 or c[0] == 0:
                        force_step_back = True
                        break

                    if c[0] == 0x22:
                        break
                    else:
                        current_token['content'] += c


            elif op == 0x84: # Data
                content = data.read(1) # Space after DATA is required, I think; store it as content.
                fields = [bytearray()]
                while True:
                    c = data.read(1)
                    if len(c) == 0 or c[0] == 0:
                        force_step_back = True
                        break
                    elif c[0] == 0x2c: # Comma
                        fields.append(bytearray())
                    elif c[0] == 0x3a: # Colon
                        force_step_back = True
                        break
                    else:
                        fields[-1] += c
                current_token['fields'] = fields
            elif op == 0x8f: # Remark
                current_token['content'] = bytearray()
                while True:
                    c = data.read(1)
                    if len(c) == 0 or c[0] == 0:
                        force_step_back = True
                        break
                    current_token['content'] += c

            tokens.append(current_token)

            if force_step_back:
                data.seek(data.tell() - 1)

        lines.append({'line_number': line_number, 'orig_addr': link_addr, 'tokens': tokens})

        data.seek(link_addr - 1)

    return lines

def pack_bytecode(lines):
    output = bytearray()
    for line in lines:
        line_data = bytearray()
        for token in line['tokens']:
            line_data += bytes([token['op']])
            if 'content' in token:
                line_data += token['content']
            if 'fields' in token:
                fields_data = bytearray()
                for field in token['fields']:
                    if len(fields_data) > 0:
                        fields_data += b','
                    fields_data += field
                line_data += fields_data

            if 'terminator' in token:
                line_data += bytes([token['terminator']])

        # Current pos + 4 for line/pointer + length of line + 1 for terminator + 1 for weird offset
        link_addr = len(output) + 4 + len(line_data) + 1 + 1

        output += int.to_bytes(link_addr, 2, byteorder='little')
        output += int.to_bytes(line['line_number'], 2, byteorder='little')
        output += line_data
        output += b'\x00'

    # Terminator
    output += b'\x00\x00\x00'

    return output


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

    track_start_address_list = []

    next_block_table = bytearray()
    directory_table = bytearray()

    with open(args.in_disk_image, 'rb') as in_file:
        # First, read the table from the D88 header that gives the start
        # address in the disk image of each track.
        MAX_TRACKS = 164
        in_file.seek(0x20)
        for _ in range(MAX_TRACKS):
            track_start_address_list.append(int.from_bytes(in_file.read(4), byteorder='little'))

        # Now, the loader specific to this disk. It seems to organize its files in "blocks"
        # that appear to be 8 sectors, or half a track, each. It has a table that says what
        # the next block should be after each block. Values greater than 0xc0 appear to be
        # terminators; I'm not sure what exactly they mean.
        in_file.seek(0x800 + 0x10)
        next_block_table += in_file.read(0x100)

        # Then, the loader's directory table is a sequence of 32-byte records spanning 4 sectors.
        in_file.seek(0x910)
        for _ in range(4):
            in_file.seek(0x10, os.SEEK_CUR)
            directory_table += in_file.read(0x100)

        # I know that the main Dragon & Princess BASIC code file is entry 11 in this table.
        # I also know that the last byte in the record (0x1f) is the first block of the file.
        # And the file size, at least in our case, is a 16-bit value at offset 0x1b.
        dnp_directory_entry = directory_table[(11 * 0x20):(12 * 0x20)]
        current_block = dnp_directory_entry[0x1f]
        file_size = int.from_bytes(dnp_directory_entry[0x1b:0x1d], byteorder='big')

        # Given that, we can just follow the blocks in the table and grab the whole file.
        # Again, each block spans 8 sectors.
        while current_block < 0xc0:
            track_index = current_block // 2
            sector_index = (current_block % 2) * 8
            address = track_start_address_list[track_index] + sector_index * 0x110

            in_file.seek(address)
            for _ in range(8):
                in_file.seek(0x10, os.SEEK_CUR)
                buf += in_file.read(0x100)

            current_block = next_block_table[current_block]

        buf = buf[:file_size]

    lines = []
    with io.BytesIO(buf) as data:
        lines = unpack_bytecode(data)

    # Build the CSVs if we need to.
    if args.update_csv:
        with open('csv/gametext.csv', 'w+', encoding='utf8') as out_file:
            writer = csv.writer(out_file, lineterminator='\n')

            for line in lines:
                string_index = 0
                for token in line['tokens']:
                    if token['op'] == 0x22:
                        try:
                            text = token['content'].decode('shift_jis')
                            row = [line['line_number'], string_index, text]

                            if text in game_text_lookup:
                                row += game_text_lookup[text]

                            writer.writerow(row)
                        except UnicodeDecodeError:
                            pass
                        string_index += 1

        with open('csv/misctext.csv', 'w+', encoding='utf8') as out_file:
            writer = csv.writer(out_file, lineterminator='\n')

            for line in lines:
                string_index = 0
                for token in line['tokens']:
                    if token['op'] == 0x84:
                        for field in token['fields']:
                            try:
                                text = field.decode('shift_jis')

                                # Ugly hack, because Python
                                is_number = True
                                try:
                                    float(text)
                                except ValueError:
                                    is_number = False

                                if not is_number:
                                    row = [line['line_number'], string_index, text]

                                    if text in misc_text_lookup:
                                        row += misc_text_lookup[text]

                                    writer.writerow(row)

                                    string_index += 1
                            except UnicodeDecodeError:
                                string_index += 1

    # Now scan through and patch in translations as needed.
    for line in lines:
        for token in line['tokens']:
            if token['op'] == 0x22:
                try:
                    text = token['content'].decode('shift_jis')
                except UnicodeDecodeError:
                    continue

                if text in game_text_lookup:
                    row = game_text_lookup[text]

                    if len(row) > 0 and len(row[0]) > 0:
                        try:
                            token['content'] = row[0].encode('shift_jis')
                        except UnicodeEncodeError:
                            print("Translated text \"{0}\" (in line {1}) could not be encoded.".format(row[0], line['line_number']))
            elif token['op'] == 0x84:
                for index, field in enumerate(token['fields']):
                    try:
                        text = field.decode('shift_jis')

                        # Ugly hack, because Python
                        is_number = True
                        try:
                            float(text)
                        except ValueError:
                            is_number = False

                        if not is_number and text in misc_text_lookup:
                            row = misc_text_lookup[text]
                            if len(row) > 0 and len(row[0]) > 0:
                                token['fields'][index] = row[0].encode('shift_jis')
                    except UnicodeDecodeError:
                        pass


    output = pack_bytecode(lines)

    print('Orig {0}, result {1}'.format(len(buf), len(output)))

    # Surgery on the directory table.
    # First, patch in the new size of the output bytecode.
    directory_table[(11 * 0x20) + 0x1b:(11 * 0x20) + 0x1d] = len(output).to_bytes(2, byteorder='big')

    # Amend the name of the file a little.
    directory_table[(11 * 0x20) + 0x12:(11 * 0x20) + 0x14] = b'EN'

    # Now, there's a game called "Donkey Gorilla" that takes up three entries in the directory
    # starting at index 17. This game doesn't seem to boot, so we're just going to get rid of it.
    directory_table[(17 * 0x20):(20 * 0x20)] = b''

    # And pad it out to compensate.
    directory_table = directory_table.ljust(0x400, b'\xff')

    # Deleting that frees up blocks 0x83 through 0x87. Let's just use 0x83 for overflow for now. Update
    # the next-block table accordingly.
    orig_terminator = next_block_table[0x5c]
    next_block_table[0x5c] = 0x83
    next_block_table[0x83] = orig_terminator


    # Then overwrite the important sectors in the output file with chunks from the local buffer.
    with open(args.out_disk_image, 'r+b') as out_file, io.BytesIO(output) as data:

        out_file.seek(0x800 + 0x10)
        out_file.write(next_block_table)

        out_file.seek(0x910)
        with io.BytesIO(directory_table) as dir_data:
            for _ in range(4):
                out_file.seek(0x10, os.SEEK_CUR)
                out_file.write(dir_data.read(0x100))

        current_block = directory_table[(11 * 0x20) + 0x1f]
        while current_block < 0xc0:
            track_index = current_block // 2
            sector_index = (current_block % 2) * 8
            address = track_start_address_list[track_index] + sector_index * 0x110

            out_file.seek(address)
            for _ in range(8):
                out_file.seek(0x10, os.SEEK_CUR)

                sector = data.read(0x100).ljust(0x100, b'\xff')
                out_file.write(sector)

            current_block = next_block_table[current_block]

        leftover_data = data.read()
        if len(leftover_data) > 0:
            raise Exception('Ran out of space! {0} bytes were not written.'.format(len(leftover_data)))
