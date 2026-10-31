#!/usr/bin/env python3
"""Extract input-book leaves and connect their roots to a master book."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import tempfile

from book_to_think_sfens import (
    NON_BOARD_MOVES, SFEN_START_PLY1, board_from_sfen, checked_push_usi, flipped_move,
    flipped_sfen, normalize_sfen_key,
)
from YaneuraOuBookLib import read_yaneuraou_book_blocks


def canonical(sfen: str) -> tuple[str, bool]:
    key = normalize_sfen_key(sfen)
    flip = flipped_sfen(key)
    return (flip, True) if flip < key else (key, False)


def children(sfen: str, stored_moves: str):
    """Follow moves stored in canonical orientation from an actual position."""
    _, flipped = canonical(sfen)
    board = board_from_sfen(sfen)
    for stored in stored_moves.splitlines():
        move = flipped_move(stored) if flipped else stored
        checked_push_usi(board, move, context=f'book position {sfen}')
        yield move, board.sfen()
        board.pop()


def create_schema(db: sqlite3.Connection) -> None:
    db.executescript('''
        CREATE TABLE input_nodes (key TEXT PRIMARY KEY, sfen TEXT NOT NULL,
                                  moves TEXT NOT NULL, incoming INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE master_nodes (key TEXT PRIMARY KEY, sfen TEXT NOT NULL, moves TEXT NOT NULL);
        CREATE TABLE roots (id INTEGER PRIMARY KEY, key TEXT UNIQUE, sfen TEXT NOT NULL,
                            cycle_seed INTEGER NOT NULL DEFAULT 0, master_visit INTEGER);
        CREATE TABLE input_visit (id INTEGER PRIMARY KEY, key TEXT UNIQUE, sfen TEXT NOT NULL,
                                  root_id INTEGER NOT NULL, parent INTEGER, move TEXT);
        CREATE TABLE leaves (visit_id INTEGER PRIMARY KEY);
        CREATE TABLE master_visit (id INTEGER PRIMARY KEY, key TEXT UNIQUE, sfen TEXT NOT NULL,
                                   parent INTEGER, move TEXT);
    ''')


def load_book(db: sqlite3.Connection, table: str, path: Path, progress: int) -> None:
    print(f'loading {table}: {path}', flush=True)
    count = moves_count = 0
    sql = (f'INSERT INTO {table}(key,sfen,moves) VALUES(?,?,?) '
           'ON CONFLICT(key) DO UPDATE SET moves = '
           f"CASE WHEN {table}.moves = '' THEN excluded.moves "
           f"WHEN excluded.moves = '' THEN {table}.moves "
           f"ELSE {table}.moves || char(10) || excluded.moves END")
    for sfen, moves in read_yaneuraou_book_blocks(str(path), ignore_book_ply=False):
        board = board_from_sfen(sfen)
        actual_sfen = board.sfen()
        key, flipped = canonical(actual_sfen)
        converted = []
        for item in moves:
            move = item.move
            if move in NON_BOARD_MOVES:
                continue
            # Reject bad moves before attempting to flip or save them.
            checked_push_usi(board, move, context=f'{path}: {actual_sfen}')
            board.pop()
            converted.append(flipped_move(move) if flipped else move)
        db.execute(sql, (key, actual_sfen, '\n'.join(converted)))
        count += 1
        moves_count += len(converted)
        if count % 10000 == 0:
            db.commit()
        if progress and count % progress == 0:
            print(f'  loaded {count:,} blocks, {moves_count:,} moves', flush=True)
    db.commit()
    unique = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
    print(f'  loaded blocks={count:,}, unique positions={unique:,}, moves={moves_count:,}', flush=True)


def find_input_roots_and_leaves(db: sqlite3.Connection, progress: int) -> None:
    print('finding input roots and traversing input book...', flush=True)
    reader = db.execute('SELECT sfen,moves FROM input_nodes')
    count = 0
    while batch := reader.fetchmany(1000):
        for sfen, moves in batch:
            for _, child in children(sfen, moves):
                key, _ = canonical(child)
                db.execute('UPDATE input_nodes SET incoming=1 WHERE key=? AND incoming=0', (key,))
            count += 1
        if progress and count % progress < len(batch):
            print(f'  input graph scanned: {count:,}', flush=True)
        db.commit()
    db.execute('INSERT INTO roots(key,sfen) SELECT key,sfen FROM input_nodes WHERE incoming=0 ORDER BY rowid')
    db.execute('INSERT INTO input_visit(key,sfen,root_id) SELECT key,sfen,id FROM roots ORDER BY id')
    db.commit()
    last_id = processed = cycle_seeds = scan_rowid = 0
    while True:
        row = db.execute('SELECT id,key,sfen,root_id FROM input_visit WHERE id>? ORDER BY id LIMIT 1', (last_id,)).fetchone()
        if row is None:
            # A component containing a cycle may have no zero-indegree vertex.
            missing = db.execute('SELECT n.rowid,n.key,n.sfen FROM input_nodes n WHERE n.rowid>? AND NOT EXISTS '
                                 '(SELECT 1 FROM input_visit v WHERE v.key=n.key) ORDER BY n.rowid LIMIT 1',
                                 (scan_rowid,)).fetchone()
            if missing is None:
                break
            scan_rowid, key, sfen = missing
            root_id = db.execute('INSERT INTO roots(key,sfen,cycle_seed) VALUES(?,?,1)', (key, sfen)).lastrowid
            db.execute('INSERT INTO input_visit(key,sfen,root_id) VALUES(?,?,?)', (key, sfen, root_id))
            cycle_seeds += 1
            continue
        visit_id, key, sfen, root_id = row
        last_id = visit_id
        stored_moves = db.execute('SELECT moves FROM input_nodes WHERE key=?', (key,)).fetchone()[0]
        child_nodes = 0
        for move, child in children(sfen, stored_moves):
            child_key, _ = canonical(child)
            if db.execute('SELECT 1 FROM input_nodes WHERE key=?', (child_key,)).fetchone() is None:
                continue
            child_nodes += 1
            db.execute('INSERT OR IGNORE INTO input_visit(key,sfen,root_id,parent,move) VALUES(?,?,?,?,?)',
                       (child_key, child, root_id, visit_id, move))
        if child_nodes == 0:
            db.execute('INSERT INTO leaves(visit_id) VALUES(?)', (visit_id,))
        processed += 1
        if processed % 10000 == 0:
            db.commit()
        if progress and processed % progress == 0:
            print(f'  input positions traversed: {processed:,}', flush=True)
    db.commit()
    roots = db.execute('SELECT count(*) FROM roots').fetchone()[0]
    leaves = db.execute('SELECT count(*) FROM leaves').fetchone()[0]
    print(f'  roots={roots:,}, cycle seeds={cycle_seeds:,}, visited={processed:,}, leaf positions={leaves:,}', flush=True)
    if not processed:
        raise ValueError('input book has no positions')


def connect_master(db: sqlite3.Connection, progress: int) -> None:
    print('searching master book from startpos...', flush=True)
    start = board_from_sfen(SFEN_START_PLY1).sfen()
    key, _ = canonical(start)
    db.execute('INSERT INTO master_visit(key,sfen) VALUES(?,?)', (key, start))
    last_id = processed = matched = 0
    total = db.execute('SELECT count(*) FROM roots').fetchone()[0]
    while matched < total:
        row = db.execute('SELECT id,key,sfen FROM master_visit WHERE id>? ORDER BY id LIMIT 1', (last_id,)).fetchone()
        if row is None:
            break
        visit_id, key, sfen = row
        last_id = visit_id
        result = db.execute('UPDATE roots SET master_visit=? WHERE key=? AND master_visit IS NULL', (visit_id, key))
        matched += result.rowcount
        stored = db.execute('SELECT moves FROM master_nodes WHERE key=?', (key,)).fetchone()
        if stored is not None:
            for move, child in children(sfen, stored[0]):
                child_key, _ = canonical(child)
                # Only registered positions and target roots need to enter the queue.
                present = db.execute('SELECT 1 FROM master_nodes WHERE key=?', (child_key,)).fetchone()
                if present is None and db.execute('SELECT 1 FROM roots WHERE key=?', (child_key,)).fetchone() is None:
                    continue
                db.execute('INSERT OR IGNORE INTO master_visit(key,sfen,parent,move) VALUES(?,?,?,?)',
                           (child_key, child, visit_id, move))
        processed += 1
        if processed % 10000 == 0:
            db.commit()
        if progress and processed % progress == 0:
            print(f'  master visited={processed:,}, roots matched={matched:,}/{total:,}', flush=True)
    db.commit()
    print(f'  master visited={processed:,}, roots matched={matched:,}, unmatched={total-matched:,}', flush=True)


def path_moves(db: sqlite3.Connection, table: str, visit_id: int) -> list[str]:
    result = []
    while True:
        parent, move = db.execute(f'SELECT parent,move FROM {table} WHERE id=?', (visit_id,)).fetchone()
        if parent is None:
            break
        result.append(move)
        visit_id = parent
    result.reverse()
    return result


def write_results(db: sqlite3.Connection, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    matched_leafs = unmatched_leafs = 0
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=output.parent, prefix='.think-sfens-', delete=False) as stream:
            tmp_name = stream.name
            rows = db.execute('SELECT v.id, r.sfen, r.master_visit FROM leaves l '
                              'JOIN input_visit v ON v.id=l.visit_id JOIN roots r ON r.id=v.root_id ORDER BY v.id')
            for visit_id, root_sfen, master_id in rows:
                tail = path_moves(db, 'input_visit', visit_id)
                if master_id is None:
                    command = 'sfen ' + root_sfen
                    unmatched_leafs += 1
                else:
                    master_sfen = db.execute('SELECT sfen FROM master_visit WHERE id=?', (master_id,)).fetchone()[0]
                    if canonical(root_sfen)[1] != canonical(master_sfen)[1]:
                        tail = [flipped_move(move) for move in tail]
                    tail = path_moves(db, 'master_visit', master_id) + tail
                    command = 'startpos'
                    matched_leafs += 1
                if tail:
                    command += ' moves ' + ' '.join(tail)
                stream.write(command + '\n')
        os.replace(tmp_name, output)
        tmp_name = None
    finally:
        if tmp_name is not None:
            os.unlink(tmp_name)
    print(f'wrote {output}: startpos={matched_leafs:,}, sfen={unmatched_leafs:,}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Find input-book leaf positions and connect their roots to a master book.')
    parser.add_argument('master', type=Path, help='master book (.db or .ybb)')
    parser.add_argument('input', type=Path, help='input book (.db or .ybb)')
    parser.add_argument('output', type=Path, help='output think_sfens.txt')
    parser.add_argument('--tmp-dir', type=Path, help='parent directory for the temporary SQLite database')
    parser.add_argument('--progress-interval', type=int, default=100000, help='progress interval; 0 disables progress')
    args = parser.parse_args()
    if args.progress_interval < 0:
        parser.error('--progress-interval must be non-negative')
    if args.output.resolve() in (args.master.resolve(), args.input.resolve()):
        parser.error('output must differ from both input books')
    with tempfile.TemporaryDirectory(prefix='book-master-', dir=args.tmp_dir) as directory:
        print(f'temporary database: {directory}', flush=True)
        with sqlite3.connect(Path(directory) / 'graph.sqlite3') as db:
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA synchronous=OFF')
            create_schema(db)
            load_book(db, 'input_nodes', args.input, args.progress_interval)
            find_input_roots_and_leaves(db, args.progress_interval)
            load_book(db, 'master_nodes', args.master, args.progress_interval)
            connect_master(db, args.progress_interval)
            write_results(db, args.output)


if __name__ == '__main__':
    main()
