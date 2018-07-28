import re
from typing import Dict, Any, Union, List, Tuple

from apscheduler.job import Job
from apscheduler.jobstores.base import ConflictingIdError, JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from none import get_bot
from none.command import call_command

from . import db, aio
from .log import logger

_scheduler: AsyncIOScheduler = None


def make_job_id(plugin_name: str, ctx_id: str,
                job_name: str = '') -> str:
    """
    Make a scheduler job id.

    :param plugin_name: the plugin that the user is calling
    :param ctx_id: context id
    :param job_name: name of the job, if not given, job id prefix is returned
    :return: job id, or job id prefix if job_name is not given
    """
    job_id = f'/{plugin_name}/{ctx_id.strip("/")}/'
    if job_name:
        if not re.fullmatch(r'[_a-zA-Z][_a-zA-Z0-9]*', job_name):
            raise ValueError(r'job name should match "[_a-zA-Z][_a-zA-Z0-9]*"')
        job_id += f'{job_name}'
    return job_id


def init() -> None:
    """
    Initialize scheduler module.

    This must be called before any plugins using database,
    and after initializing "none" module.
    """
    logger.debug('Initializing scheduler')

    global _scheduler
    _scheduler = AsyncIOScheduler(
        jobstores={
            'default': SQLAlchemyJobStore(
                url=get_bot().config.DATABASE_URL,
                tablename=db.make_table_name('core', 'apscheduler_jobs')
            )
        },
        timezone='Asia/Shanghai'
    )

    if not _scheduler.running:
        _scheduler.start()


def get_scheduler() -> AsyncIOScheduler:
    """
    Get the internal APScheduler scheduler instance.
    """
    return _scheduler


class ScheduledCommand:
    """
    Represent a command that will be run when a scheduler job
    being executing.
    """
    __slots__ = ('name', 'current_arg')

    def __init__(self, name: Union[str, Tuple[str]], current_arg: str = ''):
        self.name = name
        self.current_arg = current_arg

    def __repr__(self):
        return f'<ScheduledCommand (' \
               f'name={repr(self.name)}, ' \
               f'current_arg={repr(self.current_arg)})>'

    def __str__(self):
        return f'{self.name}' \
               f'{" " + self.current_arg if self.current_arg else ""}'


class SchedulerError(Exception):
    pass


class JobIdConflictError(ConflictingIdError, SchedulerError):
    pass


async def add_scheduled_commands(
        commands: Union[ScheduledCommand, List[ScheduledCommand]], *,
        job_id: str, ctx: Dict[str, Any],
        trigger: str,
        replace_existing: bool = False,
        misfire_grace_time: int = 360,
        apscheduler_kwargs: Dict[str, Any] = None,
        **trigger_args) -> Job:
    """
    Add commands to scheduler for scheduled execution.

    :param commands: commands to add, can be a single ScheduledCommand
    :param job_id: job id, using of make_job_id() is recommended
    :param ctx: context dict
    :param trigger: same as APScheduler
    :param replace_existing: same as APScheduler
    :param misfire_grace_time: same as APScheduler
    :param apscheduler_kwargs: other APScheduler keyword args
    :param trigger_args: same as APScheduler
    """
    commands = [commands] \
        if isinstance(commands, ScheduledCommand) else commands
    scheduler = get_scheduler()

    try:
        return await aio.run_sync_func(
            scheduler.add_job,
            _scheduled_commands_callback,
            id=job_id,
            trigger=trigger, **trigger_args,
            kwargs={'ctx': ctx, 'commands': commands},
            replace_existing=replace_existing,
            misfire_grace_time=misfire_grace_time,
            **(apscheduler_kwargs or {}))
    except ConflictingIdError:
        raise JobIdConflictError(job_id)


async def _scheduled_commands_callback(
        ctx: Dict[str, Any],
        commands: List[ScheduledCommand]) -> None:
    # get the current bot, we may not in the original running environment now
    bot = get_bot()
    for cmd in commands:
        await call_command(bot, ctx, cmd.name, current_arg=cmd.current_arg,
                           check_perm=True, disable_interaction=True)


async def get_job(job_id: str) -> Job:
    scheduler = get_scheduler()
    return await aio.run_sync_func(scheduler.get_job, job_id)


async def get_jobs(job_id_prefix: str) -> List[Job]:
    scheduler = get_scheduler()
    all_jobs = await aio.run_sync_func(scheduler.get_jobs)
    return list(filter(
        lambda j: j.id.rsplit('/', maxsplit=1)[0] == job_id_prefix.rstrip('/'),
        all_jobs
    ))


async def remove_job(job_id: str) -> bool:
    scheduler = get_scheduler()
    try:
        await aio.run_sync_func(scheduler.remove_job, job_id)
        return True
    except JobLookupError:
        return False


def get_scheduled_commands_from_job(job: Job):
    return job.kwargs['commands']
