"""row level security on every table, so the hosted REST API cannot read them

Revision ID: b58d0c9a3e77
Revises: f27c4d0b9a13
Create Date: 2026-08-24

T-3.10, and found by deploying rather than by reading.

`D-15` and `D-16` were closed by one signup (T-3.6), and that is exactly what
makes this necessary: the Postgres these tables live in is a **Supabase
project**, and a Supabase project publishes every table in the `public` schema
over PostgREST at `<project>/rest/v1/<table>`. The key that opens it is the
anon key, which is not a secret by design - it is compiled into the browser
bundle and served to everyone who opens the app (`scripts/web_env.py` says so
in its own docstring).

Alembic creates tables the way any other Postgres client would, with row level
security **off**, because outside Supabase nothing publishes them. Measured
here against the deployed project, with nothing but the public anon key:

    GET /rest/v1/alembic_version  ->  200  [{"version_num": "f27c4d0b9a13"}]
    GET /rest/v1/songs            ->  200  []

The empty list is not a refusal. It is an empty table - the deployed stack has
never finished a song - and every row that lands in it from now on would be
readable by anybody, along with `jobs`, `lyrics`, and the rest. T-3.7 gave
songs an owner and answers `404` for somebody else's; none of that is in the
path here, because this door does not go through our API at all.

Enabling row level security with **no policies** closes it: PostgREST asks as
`anon` or `authenticated` and gets nothing, for reading and for writing alike.
Our own API is unaffected - it connects as the role that owns these tables, and
an owner is exempt from RLS unless the table also says FORCE, which none of
them do. That is the whole reason this is the fix rather than revoking grants:
Supabase's default privileges hand `anon` a grant on anything new in `public`,
so a revoke is a thing to remember for every future table, and this is not.

`alembic_version` is in the list on purpose. It is the one table that had a row
in it, and it is the one that proved the schema was open.
"""

from alembic import op

revision = "b58d0c9a3e77"
down_revision = "f27c4d0b9a13"
branch_labels = None
depends_on = None

# Every table this project owns, including Alembic's own. Written out rather
# than discovered from the catalogue: a migration that enumerates the schema at
# run time does something different on every database it meets.
TABLES = (
    "songs",
    "stems",
    "jobs",
    "user_song_settings",
    "lyrics",
    "lyric_lines",
    "alembic_version",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
