#!/usr/bin/env python3
"""Extract input-book leaves and connect their roots to a master book in memory."""
from __future__ import annotations

import argparse
from array import array
import os
from pathlib import Path
import tempfile
import time

import cshogi
import numpy as np

from book_to_think_sfens import NON_BOARD_MOVES, SFEN_START_PLY1, checked_push_usi, flipped_move, flipped_sfen
from YaneuraOuBookLib import pack_sfen, read_yaneuraou_book_blocks, trim_number

EMPTY = np.uint32(0xffffffff)
FLIP = 1 << 31
MASK = FLIP - 1
BATCH = 100000


def canonical_board(board):
    sfen = trim_number(board.sfen())
    other = flipped_sfen(sfen)
    if other < sfen:
        flipped = cshogi.Board()
        flipped.set_sfen(other)
        return flipped, True
    return board, False


def board_from_key(key):
    board = cshogi.Board()
    board.set_psfen(np.frombuffer(bytes(key), dtype=cshogi.PackedSfen, count=1))
    return board


def key_and_flip(board):
    canonical, flip = canonical_board(board)
    return pack_sfen(canonical), flip


class CompactBook:
    """Packed keys and contiguous 16-bit moves; no Python object per position."""
    def __init__(self, path, progress):
        print(f'loading book: {path}', flush=True)
        raw_keys = bytearray()
        raw_offsets = array('I')
        raw_counts = array('I')
        raw_ply = array('I')
        raw_flip = array('B')
        raw_moves = array('H')
        blocks = 0
        for sfen, moves in read_yaneuraou_book_blocks(str(path), ignore_book_ply=False):
            board = cshogi.Board()
            board.set_sfen(sfen)
            canon, flip = canonical_board(board)
            raw_keys.extend(pack_sfen(canon))
            raw_offsets.append(len(raw_moves))
            raw_ply.append(max(0, board.move_number))
            raw_flip.append(flip)
            count = 0
            for item in moves:
                if item.move in NON_BOARD_MOVES:
                    continue
                checked_push_usi(board, item.move, context=f'{path}: {sfen}')
                board.pop()
                move = flipped_move(item.move) if flip else item.move
                move32 = canon.move_from_usi(move)
                if not move32 or not canon.is_legal(move32):
                    raise ValueError(f'illegal canonical move: {move} / {sfen}')
                raw_moves.append(cshogi.move16(move32))
                count += 1
            raw_counts.append(count)
            blocks += 1
            if blocks >= MASK:
                raise ValueError('too many positions for the 31-bit graph index')
            if len(raw_moves) >= int(EMPTY):
                raise ValueError('too many moves for the 32-bit edge index')
            if progress and blocks % progress == 0:
                print(f'  loaded {blocks:,} blocks / {len(raw_moves):,} moves', flush=True)
        if not blocks:
            self.keys = np.empty(0, dtype='V32')
            self.offsets = np.zeros(1, dtype=np.uint32)
            self.ply = np.empty(0, dtype=np.uint32)
            self.original_flip = np.empty(0, dtype=np.bool_)
            self.moves = np.empty(0, dtype=np.uint16)
            return
        print(f'  sorting {blocks:,} positions...', flush=True)
        unsorted_keys = np.frombuffer(raw_keys, dtype='V32')
        order = np.argsort(unsorted_keys, kind='quicksort')
        sorted_keys = unsorted_keys[order]
        del unsorted_keys, raw_keys
        # Merge duplicates (including flipped representations) without per-node objects.
        starts = np.empty(blocks, dtype=np.bool_)
        starts[0] = True
        starts[1:] = sorted_keys[1:] != sorted_keys[:-1]
        unique = int(np.count_nonzero(starts))
        self.keys = sorted_keys if unique == blocks else sorted_keys[starts]
        self.offsets = np.empty(unique + 1, dtype=np.uint32)
        self.ply = np.empty(unique, dtype=np.uint32)
        self.original_flip = np.empty(unique, dtype=np.bool_)
        ordered_moves = array('H')
        move_view = memoryview(raw_moves)
        index = -1
        for j, old in enumerate(order):
            if starts[j]:
                index += 1
                self.offsets[index] = len(ordered_moves)
                self.ply[index] = raw_ply[int(old)]
                self.original_flip[index] = raw_flip[int(old)]
            start = raw_offsets[int(old)]
            ordered_moves.frombytes(move_view[start:start + raw_counts[int(old)]].tobytes())
            if progress and (j + 1) % progress == 0:
                print(f'  sorted {j + 1:,}/{blocks:,}', flush=True)
        self.offsets[unique] = len(ordered_moves)
        self.moves = np.frombuffer(ordered_moves, dtype=np.uint16)
        print(f'  unique positions={unique:,}, moves={len(self.moves):,}', flush=True)

    def __len__(self):
        return len(self.keys)

    def lookup(self, key):
        if not len(self.keys):
            return None
        value = np.void(key)
        i = int(np.searchsorted(self.keys, value))
        return i if i < len(self.keys) and self.keys[i] == value else None

    def board(self, index):
        return board_from_key(self.keys[index])

    def move_text(self, index, move16, flip):
        board = self.board(index)
        move = board.move_from_move16(int(move16))
        if not move:
            raise ValueError('invalid move in graph')
        text = cshogi.move_to_usi(move)
        return flipped_move(text) if flip else text


def build_edges(book, extra=None, extra_roots=None, progress=0):
    """Map moves to canonical node IDs, with bit 31 carrying the orientation change."""
    if len(book) + (len(extra) if extra is not None else 0) >= MASK:
        raise ValueError('too many nodes for the 31-bit graph index')
    print(f'building graph: {len(book):,} positions, {len(book.moves):,} moves', flush=True)
    edges = np.full(len(book.moves), EMPTY, dtype=np.uint32)
    pending = bytearray()
    pending_ids = array('I')
    pending_flip = array('B')
    if extra is not None:
        root_ids = np.flatnonzero(extra_roots).astype(np.uint32)
        root_keys = extra.keys[root_ids]

    def flush():
        if not pending_ids:
            return
        queries = np.frombuffer(pending, dtype='V32')
        ids = np.frombuffer(pending_ids, dtype=np.uint32)
        flips = np.frombuffer(pending_flip, dtype=np.uint8)
        pos = np.searchsorted(book.keys, queries)
        valid = pos < len(book)
        match = np.zeros(len(pos), dtype=np.bool_)
        match[valid] = book.keys[pos[valid]] == queries[valid]
        values = np.full(len(pos), EMPTY, dtype=np.uint32)
        values[match] = pos[match].astype(np.uint32)
        if extra is not None and len(root_ids):
            missing = np.flatnonzero(~match)
            if len(missing):
                p = np.searchsorted(root_keys, queries[missing])
                valid2 = p < len(root_ids)
                hits = np.zeros(len(p), dtype=np.bool_)
                hits[valid2] = root_keys[p[valid2]] == queries[missing][valid2]
                chosen = np.flatnonzero(hits)
                values[missing[chosen]] = len(book) + root_ids[p[chosen]]
        hit = values != EMPTY
        values[hit] |= flips[hit].astype(np.uint32) * np.uint32(FLIP)
        edges[ids] = values
        # Release views before resizing the underlying arrays.
        del queries, ids, flips
        pending.clear()
        del pending_ids[:]
        del pending_flip[:]

    for index in range(len(book)):
        board = book.board(index)
        start, end = int(book.offsets[index]), int(book.offsets[index + 1])
        for edge in range(start, end):
            board.push_move16(int(book.moves[edge]))
            key, flip = key_and_flip(board)
            board.pop()
            pending.extend(key)
            pending_ids.append(edge)
            pending_flip.append(flip)
            if len(pending_ids) >= BATCH:
                flush()
        if progress and (index + 1) % progress == 0:
            print(f'  graph {index + 1:,}/{len(book):,}', flush=True)
    flush()
    return edges


def input_search(book, edges, progress):
    n = len(book)
    incoming = np.zeros(n, dtype=np.bool_)
    for start in range(0, len(edges), 1000000):
        chunk = edges[start:start + 1000000]
        valid = chunk != EMPTY
        incoming[chunk[valid] & np.uint32(MASK)] = True
    roots = array('I')
    parents = np.full(n, EMPTY, dtype=np.uint32)
    parent_move = np.zeros(n, dtype=np.uint16)
    orientation = np.zeros(n, dtype=np.bool_)
    queue = np.empty(n, dtype=np.uint32)
    qread = qwrite = 0
    for idx in np.flatnonzero(~incoming):
        i = int(idx)
        parents[i] = i
        orientation[i] = book.original_flip[i]
        queue[qwrite] = i
        qwrite += 1
        roots.append(i)
    del incoming
    leaves = array('I')
    scan = cycles = 0
    while True:
        if qread == qwrite:
            while scan < n and parents[scan] != EMPTY:
                scan += 1
            if scan == n:
                break
            parents[scan] = scan
            orientation[scan] = book.original_flip[scan]
            queue[qwrite] = scan
            qwrite += 1
            roots.append(scan)
            cycles += 1
        node = int(queue[qread]); qread += 1
        child_count = 0
        for edge in range(int(book.offsets[node]), int(book.offsets[node + 1])):
            encoded = int(edges[edge])
            if encoded == int(EMPTY):
                continue
            child_count += 1
            child = encoded & MASK
            if parents[child] == EMPTY:
                parents[child] = node
                parent_move[child] = book.moves[edge]
                orientation[child] = bool(orientation[node]) ^ bool(encoded & FLIP)
                queue[qwrite] = child
                qwrite += 1
        if not child_count:
            leaves.append(node)
        if progress and qread % progress == 0:
            print(f'  input traversed={qread:,}/{n:,}', flush=True)
    print(f'  input roots={len(roots):,}, cycle seeds={cycles:,}, leaves={len(leaves):,}', flush=True)
    return roots, leaves, parents, parent_move, orientation


def reverse_index(book, edges, total_nodes):
    print('building reverse index...', flush=True)
    # Sort compact (destination, edge) pairs in place. Avoid an int64 argsort
    # array for every edge, which is large on multi-million-position books.
    count = sum(int(np.count_nonzero(edges[start:start + 1000000] != EMPTY))
                for start in range(0, len(edges), 1000000))
    print(f'  reverse sort pairs: {count:,} x 8 bytes ({count * 8 / 1024**3:.2f} GiB)', flush=True)
    pairs = np.empty(count, dtype=[('destination', np.uint32), ('edge', np.uint32)])
    written = 0
    for start in range(0, len(edges), 1000000):
        chunk = edges[start:start + 1000000]
        valid = np.flatnonzero(chunk != EMPTY)
        end = written + len(valid)
        pairs['destination'][written:end] = chunk[valid] & np.uint32(MASK)
        pairs['edge'][written:end] = valid + start
        written = end
    print(f'  sorting {count:,} reverse edge pairs...', flush=True)
    pairs.sort(order='destination', kind='quicksort')
    offsets = np.zeros(total_nodes + 1, dtype=np.uint32)
    if count:
        changed = np.empty(count, dtype=np.bool_)
        changed[0] = True
        changed[1:] = pairs['destination'][1:] != pairs['destination'][:-1]
        starts = np.flatnonzero(changed)
        del changed
        group_counts = np.empty(len(starts), dtype=np.uint32)
        group_counts[:-1] = starts[1:] - starts[:-1]
        group_counts[-1] = count - starts[-1]
        offsets[pairs['destination'][starts] + 1] = group_counts
        del starts, group_counts
    np.cumsum(offsets, dtype=np.uint64, out=offsets)
    for start in range(0, count, 1000000):
        chunk = pairs['edge'][start:start + 1000000]
        chunk[:] = np.searchsorted(book.offsets, chunk, side='right') - 1
    reverse_parents = np.ascontiguousarray(pairs['edge'])
    del pairs
    print(f'  reverse edges={len(reverse_parents):,}', flush=True)
    return offsets, reverse_parents


def connect_master(master, edges, source, roots, bidirectional, progress):
    """Connect every reachable root, not just the first meeting point.

    In bidirectional mode the reverse pass marks all ancestors of all roots;
    the forward pass visits only marked nodes. Stopping at the first meeting
    would lose other roots when reverse paths merge.
    """
    n, total = len(master), len(master) + len(source)
    root_ids = np.frombuffer(roots, dtype=np.uint32)
    positions = np.searchsorted(master.keys, source.keys[root_ids])
    valid = positions < n
    matches = np.zeros(len(positions), dtype=np.bool_)
    matches[valid] = master.keys[positions[valid]] == source.keys[root_ids[valid]]
    target_nodes = (n + root_ids).astype(np.uint32)
    target_nodes[matches] = positions[matches].astype(np.uint32)
    if not bidirectional:
        sorted_root_ids = np.sort(root_ids)
        source_root_keys = source.keys[sorted_root_ids]
    queue = np.empty(total, dtype=np.uint32)
    reverse_seen = None
    if bidirectional:
        reverse_start = time.monotonic()
        reverse_offsets, reverse_parents = reverse_index(master, edges, total)
        print(f'reverse index time: {time.monotonic() - reverse_start:.1f}s', flush=True)
        reverse_seen = np.zeros(total, dtype=np.bool_)
        qread = qwrite = 0
        for node in target_nodes:
            i = int(node)
            if not reverse_seen[i]:
                reverse_seen[i] = True
                queue[qwrite] = i
                qwrite += 1
        print(f'reverse search: {qwrite:,} targets', flush=True)
        while qread < qwrite:
            node = int(queue[qread]); qread += 1
            for parent in reverse_parents[int(reverse_offsets[node]):int(reverse_offsets[node + 1])]:
                i = int(parent)
                if not reverse_seen[i]:
                    reverse_seen[i] = True
                    queue[qwrite] = i
                    qwrite += 1
            if progress and qread % progress == 0:
                print(f'  reverse visited={qread:,}', flush=True)
        print(f'  reverse visited={qread:,}', flush=True)
        del reverse_offsets, reverse_parents
    parents = np.full(total, EMPTY, dtype=np.uint32)
    parent_move = np.zeros(total, dtype=np.uint16)
    orientation = np.zeros(total, dtype=np.bool_)
    def result():
        matched = int(np.count_nonzero(parents[target_nodes] != EMPTY))
        print(f'  roots matched={matched:,}, unmatched={len(target_nodes) - matched:,}', flush=True)
        return target_nodes, parents, parent_move, orientation
    board = cshogi.Board(); board.set_sfen(SFEN_START_PLY1)
    start_key, start_flip = key_and_flip(board)
    initial = master.lookup(start_key)
    if initial is None:
        pos = source.lookup(start_key)
        if pos is None or pos not in roots:
            print('  startpos is absent from master; no roots matched', flush=True)
            return result()
        initial = n + pos
    if reverse_seen is not None and not reverse_seen[initial]:
        print('  no target root is reachable from startpos', flush=True)
        return result()
    parents[initial] = initial
    orientation[initial] = start_flip
    qread, qwrite = 0, 1
    queue[0] = initial
    target_mask = np.zeros(total, dtype=np.bool_)
    target_mask[target_nodes] = True
    remaining = int(np.count_nonzero(target_mask))
    print('forward search from startpos...', flush=True)
    while qread < qwrite:
        node = int(queue[qread]); qread += 1
        if target_mask[node]:
            target_mask[node] = False
            remaining -= 1
            if remaining == 0:
                break
        if node >= n:
            continue
        board = None if bidirectional else master.board(node)
        for edge in range(int(master.offsets[node]), int(master.offsets[node + 1])):
            if bidirectional:
                encoded = int(edges[edge])
                if encoded == int(EMPTY):
                    continue
            else:
                board.push_move16(int(master.moves[edge]))
                child_key, flipped = key_and_flip(board)
                board.pop()
                child_index = master.lookup(child_key)
                if child_index is None:
                    root_position = int(np.searchsorted(source_root_keys, np.void(child_key)))
                    if root_position >= len(source_root_keys) or source_root_keys[root_position] != np.void(child_key):
                        continue
                    child_index = n + int(sorted_root_ids[root_position])
                encoded = child_index | (FLIP if flipped else 0)
            child = encoded & MASK
            if (reverse_seen is None or reverse_seen[child]) and parents[child] == EMPTY:
                parents[child] = node
                parent_move[child] = master.moves[edge]
                orientation[child] = bool(orientation[node]) ^ bool(encoded & FLIP)
                queue[qwrite] = child
                qwrite += 1
        if progress and qread % progress == 0:
            print(f'  forward visited={qread:,}', flush=True)
    print(f'  forward visited={qread:,}', flush=True)
    return result()


def path_moves(book, parents, moves, orientation, node):
    chain = []
    while int(parents[node]) != node:
        parent = int(parents[node])
        if parent == int(EMPTY):
            raise ValueError('broken parent chain')
        chain.append(book.move_text(parent, moves[node], bool(orientation[parent])))
        node = parent
    chain.reverse()
    return node, chain


def write_results(output, source, roots, leaves, source_search, master, master_search, progress=100000):
    parents, moves, orientation = source_search
    target_nodes, master_parents, master_moves, master_orientation = master_search
    # Index each source root without a Python dictionary.
    root_targets = np.full(len(source), EMPTY, dtype=np.uint32)
    root_targets[np.frombuffer(roots, dtype=np.uint32)] = target_nodes
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    matched = unmatched = 0
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=output.parent,
                                         prefix='.think-sfens-', delete=False) as stream:
            temporary = stream.name
            for leaf in leaves:
                root, tail = path_moves(source, parents, moves, orientation, int(leaf))
                target = int(root_targets[root])
                if master_parents[target] == EMPTY:
                    board = source.board(root)
                    sfen = flipped_sfen(board.sfen()) if source.original_flip[root] else trim_number(board.sfen())
                    fields = sfen.split()
                    fields.append(str(int(source.ply[root])))
                    command = 'sfen ' + ' '.join(fields)
                    unmatched += 1
                else:
                    if bool(master_orientation[target]) != bool(source.original_flip[root]):
                        tail = [flipped_move(move) for move in tail]
                    # Virtual targets can have a master parent, but no moves of their own.
                    _, head = path_moves(master, master_parents, master_moves, master_orientation, target)
                    tail = head + tail
                    command = 'startpos'
                    matched += 1
                if tail:
                    command += ' moves ' + ' '.join(tail)
                stream.write(command + '\n')
                written = matched + unmatched
                if progress and written % progress == 0:
                    print(f'  wrote {written:,}/{len(leaves):,} leaves', flush=True)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    print(f'wrote {output}: startpos={matched:,}, sfen={unmatched:,}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Find input-book leaves and connect roots to a master book in memory.')
    parser.add_argument('master', type=Path, help='master book (.db or .ybb)')
    parser.add_argument('input', type=Path, help='input book (.db or .ybb)')
    parser.add_argument('output', type=Path, help='output think_sfens.txt')
    parser.add_argument('--progress-interval', type=int, default=100000)
    parser.add_argument('--search-mode', choices=('bidirectional', 'forward'), default='bidirectional',
                        help='master traversal (default: bidirectional)')
    args = parser.parse_args()
    if args.progress_interval < 0:
        parser.error('--progress-interval must be non-negative')
    if args.output.resolve() in (args.master.resolve(), args.input.resolve()):
        parser.error('output must differ from both input books')
    start = time.monotonic()
    if array('I').itemsize != 4 or array('H').itemsize != 2:
        raise RuntimeError('this platform does not provide 32-bit I and 16-bit H arrays')
    source = CompactBook(args.input, args.progress_interval)
    if not len(source):
        raise ValueError('input book has no positions')
    source_edges = build_edges(source, progress=args.progress_interval)
    roots, leaves, source_parents, source_moves, source_orientation = input_search(source, source_edges, args.progress_interval)
    del source_edges
    del source.moves, source.offsets
    root_mask = np.zeros(len(source), dtype=np.bool_)
    root_mask[np.frombuffer(roots, dtype=np.uint32)] = True
    master = CompactBook(args.master, args.progress_interval)
    # The master's original ply and orientation are not needed for path reconstruction.
    del master.ply, master.original_flip
    master_edges = build_edges(master, source, root_mask, args.progress_interval) if args.search_mode == 'bidirectional' else None
    del root_mask
    search_start = time.monotonic()
    master_search = connect_master(master, master_edges, source, roots, args.search_mode == 'bidirectional', args.progress_interval)
    print(f'master search time: {time.monotonic() - search_start:.1f}s', flush=True)
    del master_edges
    write_results(args.output, source, roots, leaves,
                  (source_parents, source_moves, source_orientation), master, master_search, args.progress_interval)
    print(f'elapsed: {time.monotonic() - start:.1f}s', flush=True)


if __name__ == '__main__':
    main()
