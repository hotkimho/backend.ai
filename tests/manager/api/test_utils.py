import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Union

import pytest
import sqlalchemy
from dateutil.tz import tzutc

from ai.backend.manager.api.utils import call_non_bursty, mask_sensitive_keys
from ai.backend.manager.models import kernels, verify_dotfile_name, verify_vfolder_name
from ai.backend.manager.models.utils import sql_json_merge


@pytest.mark.asyncio
async def test_call_non_bursty():
    key = 'x'
    execution_count = 0

    async def execute():
        nonlocal execution_count
        await asyncio.sleep(0)
        execution_count += 1

    # ensure reset
    await asyncio.sleep(0.11)

    # check run as coroutine
    execution_count = 0
    with pytest.raises(TypeError):
        await call_non_bursty(key, execute())

    # check run as coroutinefunction
    execution_count = 0
    await call_non_bursty(key, execute)
    assert execution_count == 1
    await asyncio.sleep(0.11)

    # check burstiness control
    execution_count = 0
    for _ in range(129):
        await call_non_bursty(key, execute)
    assert execution_count == 3
    await asyncio.sleep(0.01)
    await call_non_bursty(key, execute)
    assert execution_count == 3
    await asyncio.sleep(0.11)
    await call_non_bursty(key, execute)
    assert execution_count == 4
    for _ in range(64):
        await call_non_bursty(key, execute)
    assert execution_count == 5


def test_vfolder_name_validator():
    assert not verify_vfolder_name('.bashrc')
    assert not verify_vfolder_name('.terminfo')
    assert verify_vfolder_name('bashrc')
    assert verify_vfolder_name('.config')
    assert verify_vfolder_name('bin')
    assert verify_vfolder_name('boot')
    assert verify_vfolder_name('root')
    assert not verify_vfolder_name('/bin')
    assert not verify_vfolder_name('/boot')
    assert not verify_vfolder_name('/root')
    assert verify_vfolder_name('/home/work/bin')
    assert verify_vfolder_name('/home/work/boot')
    assert verify_vfolder_name('/home/work/root')
    assert verify_vfolder_name('home/work')


def test_dotfile_name_validator():
    assert not verify_dotfile_name('.terminfo')
    assert not verify_dotfile_name('.config')
    assert not verify_dotfile_name('.ssh/authorized_keys')
    assert verify_dotfile_name('.bashrc')
    assert verify_dotfile_name('.ssh/id_rsa')


def test_mask_sensitive_keys():
    a = {'a': 123, 'my-Secret': 'hello'}
    b = mask_sensitive_keys(a)
    # original is untouched
    assert a['a'] == 123
    assert a['my-Secret'] == 'hello'
    # cloned has masked fields
    assert b['a'] == 123
    assert b['my-Secret'] == '***'


async def _select_kernel_row(
    conn: sqlalchemy.ext.asyncio.engine.AsyncConnection,
    session_id: Union[str, uuid.UUID],
):
    query = kernels.select().select_from(kernels).where(kernels.c.session_id == session_id)
    kernel, *_ = await conn.execute(query)
    return kernel


@pytest.mark.asyncio
async def test_sql_json__default(session_info):
    session_id, conn = session_info
    expected: Optional[Dict[str, Any]] = None
    kernel = await _select_kernel_row(conn, session_id)
    assert kernel is not None
    assert kernel.status_history == expected


@pytest.mark.asyncio
async def test_sql_json__deeper_object(session_info):
    session_id, conn = session_info
    timestamp = datetime.now(tzutc()).isoformat()
    expected = {
        "kernel": {
            "session": {
                "PENDING": timestamp,
                "PREPARING": timestamp,
            },
        },
    }
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            ("kernel", "session"),
            {
                "PENDING": timestamp,
                "PREPARING": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    kernel = await _select_kernel_row(conn, session_id)
    assert kernel is not None
    assert kernel.status_history == expected


@pytest.mark.asyncio
async def test_sql_json__append_values(session_info):
    session_id, conn = session_info
    timestamp = datetime.now(tzutc()).isoformat()
    expected = {
        "kernel": {
            "session": {
                "PENDING": timestamp,
                "PREPARING": timestamp,
                "TERMINATED": timestamp,
                "TERMINATING": timestamp,
            },
        },
    }
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            ("kernel", "session"),
            {
                "PENDING": timestamp,
                "PREPARING": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            ("kernel", "session"),
            {
                "TERMINATING": timestamp,
                "TERMINATED": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    kernel = await _select_kernel_row(conn, session_id)
    assert kernel is not None
    assert kernel.status_history == expected


@pytest.mark.asyncio
async def test_sql_json__kernel_status_history(session_info):
    session_id, conn = session_info
    timestamp = datetime.now(tzutc()).isoformat()
    expected = {
        "PENDING": timestamp,
        "PREPARING": timestamp,
        "TERMINATING": timestamp,
        "TERMINATED": timestamp,
    }
    query = kernels.update().values({
        # "status_history": sqlalchemy.func.coalesce(sqlalchemy.text("'{}'::jsonb")).concat(
        #     sqlalchemy.func.cast(
        #         {"PENDING": timestamp, "PREPARING": timestamp},
        #         sqlalchemy.dialects.postgresql.JSONB,
        #     ),
        # ),
        "status_history": sql_json_merge(
            kernels.c.status_history,
            (),
            {
                "PENDING": timestamp,
                "PREPARING": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            (),
            {
                "TERMINATING": timestamp,
                "TERMINATED": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    kernel = await _select_kernel_row(conn, session_id)
    assert kernel is not None
    assert kernel.status_history == expected


@pytest.mark.asyncio
async def test_sql_json__mixed_formats(session_info):
    session_id, conn = session_info
    timestamp = datetime.now(tzutc()).isoformat()
    expected = {
        "PENDING": timestamp,
        "kernel": {
            "PREPARING": timestamp,
        },
    }
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            (),
            {
                "PENDING": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    kernel = await _select_kernel_row(conn, session_id)
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            ("kernel",),
            {
                "PREPARING": timestamp,
            },
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    kernel = await _select_kernel_row(conn, session_id)
    assert kernel is not None
    assert kernel.status_history == expected


@pytest.mark.asyncio
async def test_sql_json__json_serializable_types(session_info):
    session_id, conn = session_info
    expected = {
        "boolean": True,
        "integer": 10101010,
        "float": 1010.1010,
        "string": "10101010",
        # "bytes": b"10101010",
        "list": [
            10101010,
            "10101010",
        ],
        "dict": {
            "10101010": 10101010,
        },
    }
    query = kernels.update().values({
        "status_history": sql_json_merge(
            kernels.c.status_history,
            (),
            expected,
        ),
    }).where(kernels.c.session_id == session_id)
    await conn.execute(query)
    kernel = await _select_kernel_row(conn, session_id)
    assert kernel is not None
    assert kernel.status_history == expected
