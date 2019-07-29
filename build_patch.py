import argparse
import csv
import io
import os
import shutil
import sys
import textwrap

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

def unpack_operations(op_string):
    op_list = []
    for c in op_string:
        op_list.append({'op': c})
    return op_list

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


def update_random_string(line, string_index, string_count, length_index_1, length_index_2):
    string_length = len(line['tokens'][string_index]['content'])
    line['tokens'][length_index_1]['content'] = int.to_bytes(string_length // string_count, 1, byteorder='big')
    line['tokens'][length_index_2]['content'] = line['tokens'][length_index_1]['content']

if __name__ == '__main__':

    parser = argparse.ArgumentParser('Main patch build for Dragon & Princess')
    parser.add_argument('in_disk_image', help='Disk image to scan for original text.')
    parser.add_argument('out_disk_image', help='Output disk image. Will be overwritten if already present.')

    parser.add_argument('--update-csv', help='Whether the CSV files should be created/updated with the strings found in the scan. Will not overwrite old entries.', action='store_true')
    parser.add_argument('--easy-mode', help='Whether the game data should be modified to make the game easier (for testing!)', action='store_true')

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


    # These are changes that happen before the translations are added.
    for line in lines:
        line_number = line['line_number']

        # These two changes allocate the default name array, and use the default name array to assign names.
        if line_number == 160:
            line['tokens'] += unpack_operations(b',DN$(MN)')
        elif line_number == 303:
            line['tokens'][67:80] = unpack_operations(b'DN$(I)')

        # There's a line in the throne room constructed conditionally based on whether the
        # princess is supposed to be present. Shift things around so it just has two
        # whole separate strings to simplify English grammar.
        elif line_number == 2640:
            temp_ops = line['tokens'][12:17]
            line['tokens'][15:18] = []
            line['tokens'][30:31] = []
            line['tokens'] += temp_ops
        elif line_number == 2641:
            line['tokens'][0:3] = []

        # These lines print the names of shops present in the location in town... they contain a prefix
        # string that isn't necessary in English. Remove it.
        elif line_number == 2840 or line_number == 2841 or line_number == 2842:
            del line['tokens'][7]

        # As part of easy mode, this disables the check for random encounters.
        elif line_number == 5510:
            if args.easy_mode:
                line['tokens'] = [{'op': 0x8f, 'content': b'Encounters disabled!'}]

        # These changes all pertain to the title screen. Moving around a bunch of coordinates to make room for
        # patch-specific credits.
        elif line_number == 18020:
            # This one just nudges one line up.
            line['tokens'][49]['op'] = 0x13
        elif line_number == 18050:

            # This is the complicated one. First, there's a 'presented by' string that's split across three lines
            # in the original. Join those up, adjust the spacing accordingly, and delete the extra commands.
            combined_string = line['tokens'][13]['content'] + b' ' + line['tokens'][31]['content'] + b' ' + line['tokens'][49]['content']

            line['tokens'][2]['op'] = ((40 - len(combined_string)) // 2) + 0x11
            del line['tokens'][2]['content']
            line['tokens'][8]['op'] = 0x12
            line['tokens'][13]['content'] = combined_string

            line['tokens'][18:54] = []

            # Now insert commands for the lines we're injecting.
            ops_to_insert = []
            new_credit_lines = [(3, b'EN translation patch 1.0'), (1, b'by Laszlo Benyi & NLeseul')]
            if args.easy_mode:
                new_credit_lines.append((1, b'EASY MODE!!'))

            for y_spacing, new_credit_line in new_credit_lines:
                x_coord = ((40 - len(new_credit_line)) // 2)
                ops_to_insert += unpack_operations(b'X\xf1')                   # X=
                if x_coord <= 10:
                    ops_to_insert.append({'op': x_coord + 0x11})
                else:
                    ops_to_insert.append({'op': 0xf, 'content': bytes([x_coord])})
                ops_to_insert += unpack_operations(b':Y\xf1Y\xf3')             # :Y=Y+
                ops_to_insert.append({'op': y_spacing + 0x11})
                ops_to_insert += unpack_operations(b':M$\xf1')                 # :M$=
                ops_to_insert.append({'op': 0x22, 'terminator': 0x22, 'content': new_credit_line})
                ops_to_insert += unpack_operations(b':\x8d\x0eDH:')            # :GOSUB18500:

            line['tokens'][36:36] = ops_to_insert

            # Finally, if we haven't added the extra "easy mode" line, nudge down the final line ('press any key').
            if not args.easy_mode:
                line['tokens'][84]['op'] = 0x15

        # This line contains the initial stats of the characters. The change fills them in with high values
        # for easy mode if necessary.
        elif line_number == 20160:
            if args.easy_mode:
                line['tokens'][0]['fields'] = [
                    b'127', b'127', b'100', b'127', b'2.0',
                    b'127', b'127', b'100', b'127', b'2.0',
                    b'127', b'127', b'100', b'127', b'2.0',
                    b'127', b'127', b'100', b'127', b'2.0',
                    b'127', b'127', b'100', b'127', b'2.0',
                ]



    # Add a line containing the data for the default names array, and a line to read it in on initialization.
    lines.append({
        'line_number': 20165,
        'tokens': [
            {'op': 0x84, 'content': b' ', 'fields': [b'Gombe', b'Jirosaku', b'Tarosaku', b'Yosaku', b'Goemon']}
        ]
    })
    lines.append({
        'line_number': 221,
        'tokens':
            # FOR I=0 TO MN:
            unpack_operations(b'\x82I\xf1\x11 \xdc MN:') +

            # READDN$(I):
            unpack_operations(b'\x87DN$(I):') +

            # NEXT
            unpack_operations(b'\x83')
    })

    lines.sort(key=lambda line: line['line_number'])

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


    # These are changes that happen after the translations are added.
    cached_line_1640 = None
    for line in lines:
        line_number = line['line_number']

        # These lines contain multiple lines of text packed into one string, with a random selection of one substring.
        # The syntax is always something like MID$("String1String2String3", INT(RND(3)+1)*7, 7).
        # We need to adjust the length that it uses for the substrings.
        if line_number == 570:
            update_random_string(line, 4, 5, 18, 22)
        elif line_number == 1395:
            update_random_string(line, 17, 2, 31, 35)
        elif line_number == 1610:
            update_random_string(line, 13, 5, 27, 31)
        elif line_number == 1620:
            update_random_string(line, 25, 2, 39, 43)
        elif line_number == 1630:
            update_random_string(line, 13, 2, 27, 31)
        elif line_number == 1640:
            cached_line_1640 = line
        elif line_number == 1650:
            # This is a slightly strange case. They compute the string index
            # on 1640 and then use it in 1650. We need to check the length of the
            # new string here and then put it back in 1640.
            string_length_bytes = int.to_bytes(len(line['tokens'][4]['content']) // 6, 1, byteorder='big')
            line['tokens'][8]['content'] = string_length_bytes
            cached_line_1640['tokens'][26]['content'] = string_length_bytes
            cached_line_1640['tokens'][47]['content'] = string_length_bytes
        elif line_number == 1760:
            update_random_string(line, 7, 2, 21, 25)
        elif line_number == 2105:
            update_random_string(line, 56, 2, 70, 74)
        elif line_number == 2200:
            update_random_string(line, 38, 2, 52, 56)
        elif line_number == 5250:
            update_random_string(line, 4, 3, 18, 22)

    # Another pass through all the data... split up text that's too long to
    # fit in 40 characters. We can do this by breaking it up into multiple strings
    # and putting semicolons between them; the BASIC parser seems smart enough to
    # line break if the next string would force a word wrap.
    for line in lines:

        # Skip special text... either multiple strings packed together, or combat text.
        if line['line_number'] in (570,1040,1395,1610,1620,1630,1650,1760,2105,2200,5250,6050,6115,6920):
            continue

        new_tokens = []

        for token_index, token in enumerate(line['tokens']):
            if token['op'] == 0x22:
                try:
                    text = token['content'].decode('shift-jis')
                    max_length = 20 if line['line_number'] == 360 and token_index == 4 else 40
                    if len(text) > max_length:
                        for split_text_index, split_text in enumerate(textwrap.wrap(text, max_length, drop_whitespace=False)):
                            if split_text_index > 0:
                                new_tokens.append({'op': 0x3b})
                            new_tokens.append({'op': 0x22, 'terminator': 0x22, 'content': split_text.encode('shift-jis')})
                        #print(line['line_number'], token_index, )
                    else:
                        new_tokens.append(token)
                except UnicodeDecodeError:
                    new_tokens.append(token)
            else:
                new_tokens.append(token)

        #if len(new_tokens) != len(line['tokens']):
            #print(line['tokens'], new_tokens)
        line['tokens'] = new_tokens

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

    # Deleting that frees up blocks 0x83 through 0x87. Let's just use 0x83~0x84 for overflow for now. Update
    # the next-block table accordingly.
    orig_terminator = next_block_table[0x5c]
    next_block_table[0x5c] = 0x83
    next_block_table[0x83] = 0x84
    next_block_table[0x84] = orig_terminator


    # Make a new copy of the input image at the output file name.
    shutil.copyfile(args.in_disk_image, args.out_disk_image)

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
