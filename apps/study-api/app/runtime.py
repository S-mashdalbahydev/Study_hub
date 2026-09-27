"""Shared wiring for the API process and the worker process."""
from types import SimpleNamespace

from . import library, research
from .catalog import TutorCatalog
from .db import Database
from .library import Profiles
from .providers import HostedTutor, LocalTutor, OllamaEmbeddings
from .storage import LocalStorage

# Job kind -> handler. "on_failure:<kind>" runs once a job has failed for good.
HANDLERS = {
    'ingest': library.handle_ingest,
    'research': research.handle_research,
    'on_failure:ingest': library.on_ingest_failure,
    'on_failure:research': research.on_research_failure,
}
QUEUES = ('ingest', 'research')


def build(settings, client, db: Database):
    embeddings = OllamaEmbeddings(client, settings)
    return SimpleNamespace(settings=settings, db=db, client=client, storage=LocalStorage(settings.objects),
                           embeddings=embeddings, profiles=Profiles(embeddings), tutor=HostedTutor(client, settings),
                           local_tutor=LocalTutor(client), catalog=TutorCatalog(client, settings))
