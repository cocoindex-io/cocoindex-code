"""The chunk-size ceiling: what protects the embedder from an unsplittable line.

`RecursiveSplitter` treats `chunk_size` as a target, not a bound — a line with
no separator it recognises comes back whole. A Godot `.tscn` stores a packed
array as a single 61k-character line, and feeding that chunk to a long-context
embedder is quadratic in attention: it took the daemon to 106 GB of commit and
killed it. `cap_chunk_size` is the bound.
"""

from __future__ import annotations

from cocoindex.resources.chunk import Chunk, TextPosition

from cocoindex_code.indexer import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    cap_chunk_size,
    splitter,
)


def _packed_array_line(values: int = 20_000) -> str:
    """A line shaped like the one that kills the daemon: no separator to cut on.

    Godot writes packed arrays this way, and so does any minifier. Generated
    rather than committed as a fixture, so the test carries no 60 KB blob.
    """
    return '[sub_resource type="ArrayMesh"]\nsurfaces/0 = ' + ",".join(
        str(i % 97) for i in range(values)
    )


def _chunk(text: str, *, line: int = 1, column: int = 1, char_offset: int = 0) -> Chunk:
    start = TextPosition(
        byte_offset=char_offset,
        char_offset=char_offset,
        line=line,
        column=column,
    )
    end = TextPosition(
        byte_offset=start.byte_offset + len(text.encode("utf-8")),
        char_offset=start.char_offset + len(text),
        line=line + text.count("\n"),
        column=column + len(text),
    )
    return Chunk(text=text, start=start, end=end)


def test_chunks_within_the_ceiling_are_returned_unchanged() -> None:
    chunks = [_chunk("a" * 10), _chunk("b" * CHUNK_SIZE)]

    assert cap_chunk_size(chunks) is chunks


def test_an_oversized_chunk_is_cut_into_even_pieces() -> None:
    # A remainder-sized tail would be an embedding of noise, so the pieces are
    # evened out instead: 2007 characters become three of 669, not 1000/1000/7.
    text = "x" * (CHUNK_SIZE * 2 + 7)

    capped = cap_chunk_size([_chunk(text)])

    assert len(capped) == 3
    assert all(len(c.text) <= CHUNK_SIZE for c in capped)
    assert all(len(c.text) >= MIN_CHUNK_SIZE for c in capped)
    assert "".join(c.text for c in capped) == text


def test_the_smallest_oversized_chunk_still_clears_the_minimum() -> None:
    # One character over the ceiling is the worst case for an even split.
    capped = cap_chunk_size([_chunk("x" * (CHUNK_SIZE + 1))])

    assert len(capped) == 2
    assert all(MIN_CHUNK_SIZE <= len(c.text) <= CHUNK_SIZE for c in capped)


def test_pieces_carry_positions_forward() -> None:
    # Two lines, the first long enough to force a cut inside it.
    head = "y" * (CHUNK_SIZE + 3)
    text = head + "\nsecond"

    capped = cap_chunk_size([_chunk(text)])

    assert len(capped) == 2
    first, second = capped
    assert first.start.line == 1
    assert first.start.char_offset == 0
    # The cut lands inside line 1, so the next piece still starts on line 1.
    assert second.start.line == 1
    assert second.start.char_offset == len(first.text)
    # The tail spans the newline and ends on line 2.
    assert second.end.line == 2
    assert second.end.column == len("second") + 1
    assert second.end.char_offset == len(text)


def test_byte_offsets_count_bytes_not_characters() -> None:
    # Two bytes per character, so the byte offsets run ahead of the char ones.
    text = "д" * (CHUNK_SIZE + 1)

    capped = cap_chunk_size([_chunk(text)])

    assert len(capped) == 2
    assert capped[1].start.byte_offset == len(capped[0].text) * 2
    assert capped[1].end.byte_offset == len(text.encode("utf-8"))


def test_a_custom_limit_is_honoured() -> None:
    capped = cap_chunk_size([_chunk("z" * 10)], limit=4)

    assert len(capped) == 3
    assert all(len(c.text) <= 4 for c in capped)
    assert "".join(c.text for c in capped) == "z" * 10


def test_the_real_pathological_shape_is_bounded() -> None:
    # One 61k-character line, the shape that killed the daemon.
    text = "p" * 60_942

    capped = cap_chunk_size([_chunk(text)])

    assert max(len(c.text) for c in capped) <= CHUNK_SIZE
    assert min(len(c.text) for c in capped) >= MIN_CHUNK_SIZE
    assert "".join(c.text for c in capped) == text


# --- against the real splitter ----------------------------------------------
# The tests above drive cap_chunk_size directly, so they would still pass if
# the call disappeared from process_file. These two pin the actual pipeline:
# the first records what the splitter really does with such a line (and fails
# loudly if that ever changes), the second is the regression that matters.


def test_the_splitter_really_does_exceed_its_own_chunk_size() -> None:
    content = _packed_array_line()

    chunks = splitter.split(
        content,
        chunk_size=CHUNK_SIZE,
        min_chunk_size=MIN_CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        language="text",
    )

    assert max(len(c.text) for c in chunks) > CHUNK_SIZE * 10


def test_splitter_output_is_bounded_once_capped() -> None:
    content = _packed_array_line()

    chunks = cap_chunk_size(
        splitter.split(
            content,
            chunk_size=CHUNK_SIZE,
            min_chunk_size=MIN_CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            language="text",
        )
    )

    assert max(len(c.text) for c in chunks) <= CHUNK_SIZE
    # Positions stay inside the document they describe.
    assert chunks[0].start.char_offset == 0
    assert max(c.end.char_offset for c in chunks) <= len(content)
    assert all(c.start.line >= 1 and c.start.column >= 1 for c in chunks)
