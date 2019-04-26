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
    

if __name__ == '__main__':
    
    buf = bytearray()
    
    with open(sys.argv[1], 'rb') as in_file:
    
        in_file.seek(0x2a230)
        
        while in_file.tell() < 0x31600:
            in_file.seek(0x10, os.SEEK_CUR)
            buf += in_file.read(0x100)

    parse_state = InstructionsParseState.OUTSIDE_QUOTES
    
    game_text_bounds = []
    misc_text_bounds = []
    
    current_range_begin = None
    
    with io.BytesIO(buf) as data:
        while True:
            c = data.read(1)
            if c is None or len(c) == 0:
                break
            elif parse_state == InstructionsParseState.OUTSIDE_QUOTES:
                if c == b'\x91':
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
                
    with open('csv/gametext.csv', 'w', encoding='utf8') as out_file:
        writer = csv.writer(out_file, lineterminator='\n')

        for bounds in game_text_bounds:
            try:
                writer.writerow([buf[bounds[0]:bounds[1]].decode('shift_jis')])
            except UnicodeDecodeError:
                pass
            
    with open('csv/misctext.csv', 'w', encoding='utf8') as out_file:
        writer = csv.writer(out_file, lineterminator='\n')

        for bounds in misc_text_bounds:
            try:
                writer.writerow([buf[bounds[0]:bounds[1]].decode('shift_jis')])
            except UnicodeDecodeError:
                pass
