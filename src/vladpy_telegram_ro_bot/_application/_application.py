import logging
import typing
import functools
import concurrent.futures

import telegram
import telegram.ext
import telegram.constants
import pydantic
import google.oauth2.service_account # type: ignore

from vladpy_telegram_ro_bot._application._types import ApplicationType
from vladpy_telegram_ro_bot._application._initiate_logs import initiate_logs
from vladpy_telegram_ro_bot._application._defaults._application_defaults import ApplicationDefaults
from vladpy_telegram_ro_bot._application._bot import Bot
from vladpy_telegram_ro_bot._application._text_invariant._command import Command
from vladpy_telegram_ro_bot._application._text_invariant._reply import Reply
from vladpy_telegram_ro_bot._application._config._telegram_config import TelegramConfig, TelegramConfigPydantic
from vladpy_telegram_ro_bot._application._config._bot_config import BotConfig, BotConfigPydantic


# TODO fix: graceful shutdown
# TODO fix: bugs from logs
# TODO fix: message to admin in case of an error

# TODO feature: webhook (though duplicate messages might arrive)


class Application:


	def __init__(self,) -> None:

		self.__logger = logging.getLogger('vladpy_telegram_ro_bot.Application')

		self.__logger.info('init')

		self.__bot_config: typing.Optional[BotConfig] = None
		self.__telegram_config: typing.Optional[TelegramConfig] = None
		self.__gcloud_credentials: typing.Optional[google.oauth2.service_account.Credentials] = None

		self.__application: typing.Optional[ApplicationType] = None

		self.__bot: typing.Optional[Bot] = None

		self.__background_executor: typing.Optional[concurrent.futures.ProcessPoolExecutor] = None


	def run(self,) -> None:

		initiate_logs()

		self.__read_config()
		self.__create_appplication()
		assert self.__application is not None

		self.__logger.info('run begin')
		self.__application.run_polling()
		self.__logger.info('run end')


	def __read_config(self,) -> None:

		self.__logger.info('read config begin')

		with (
				open(
					'config/.stash/bot.json',
					mode='rt',
					encoding='utf8',
				)
			) as file_obj:

			self.__bot_config = (
				pydantic.TypeAdapter(BotConfigPydantic).validate_json(file_obj.read())
			)

		self.__logger.info('bot config read')

		with (
				open(
					'config/.stash/telegram.json',
					mode='rt',
					encoding='utf8',
				)
			) as file_obj:

			self.__telegram_config = (
				pydantic.TypeAdapter(TelegramConfigPydantic).validate_json(file_obj.read())
			)

		self.__logger.info('telegram token read')

		self.__gcloud_credentials = (
			google.oauth2.service_account.Credentials.from_service_account_file(
				filename='config/.stash/gcloud.json',
			)
		)

		self.__logger.info('gcloud credentials read')

		self.__logger.info('read config end')


	def __create_appplication(self,) -> None:

		self.__logger.info('create application begin')

		assert self.__bot_config is not None
		assert self.__telegram_config is not None
		assert self.__gcloud_credentials is not None

		self.__background_executor = (
			concurrent.futures.ProcessPoolExecutor(
				max_workers=1,
			)
		)

		self.__bot = (
			Bot(
				config=self.__bot_config,
				gcloud_credentials=self.__gcloud_credentials,
				background_executor=self.__background_executor,
			)
		)

		self.__application = (
			telegram.ext.ApplicationBuilder()
			.post_init(self.__initialize_application)
			.post_shutdown(self.__shutdown_application)
			.token(self.__telegram_config.token)
			.concurrent_updates(True)
			.rate_limiter(telegram.ext.AIORateLimiter())
			.defaults(telegram.ext.Defaults(
				block=False,
				parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
			))
			.connect_timeout(ApplicationDefaults.connect_timeout.total_seconds())
			.pool_timeout(ApplicationDefaults.pool_timeout.total_seconds())
			.read_timeout(ApplicationDefaults.read_timeout.total_seconds())
			.write_timeout(ApplicationDefaults.write_timeout.total_seconds())
			.get_updates_connect_timeout(ApplicationDefaults.get_updates_connect_timeout.total_seconds())
			.get_updates_pool_timeout(ApplicationDefaults.get_updates_pool_timeout.total_seconds())
			.get_updates_read_timeout(ApplicationDefaults.get_updates_read_timeout.total_seconds())
			.get_updates_write_timeout(ApplicationDefaults.get_updates_write_timeout.total_seconds())
			.build()
		)

		self.__logger.info('create application end')


	async def __initialize_application(
			self,
			application: ApplicationType,
		) -> None:

		self.__logger.info('initialize begin')

		assert self.__bot_config is not None
		assert self.__bot is not None
		assert application.bot is not None

		await application.bot.set_my_commands(
			commands=tuple(Command.command_sequence(None)),
		)

		await application.bot.set_my_commands(
			commands=tuple(Command.command_sequence('ru')),
			language_code='ru',
		)

		self.__logger.info('bot commands set')

		await application.bot.set_my_description(
			description=Reply.description(None),
		)

		await application.bot.set_my_description(
			description=Reply.description('ru'),
			language_code='ru',
		)

		await application.bot.set_my_short_description(
			short_description=Reply.short_description(None),
		)

		await application.bot.set_my_short_description(
			short_description=Reply.short_description('ru'),
			language_code='ru',
		)

		self.__logger.info('bot description set')

		application.add_error_handler(self.__handle_error)

		usernames_whitelist_filter = (
			telegram.ext.filters.User(
				allow_empty=(self.__bot_config.usernames_whitelist is None),
				username=self.__bot_config.usernames_whitelist,
			)
		)

		for command in Command.command_sequence(None):

			application.add_handler(
				telegram.ext.CommandHandler(
					command=command.command,
					filters=(
						telegram.ext.filters.COMMAND
						& telegram.ext.filters.USER
						& telegram.ext.filters.CHAT
						& (~telegram.ext.filters.VIA_BOT)
						& usernames_whitelist_filter
					),
					callback=functools.partial(self.__bot.handle_command, command,),
					block=False,
					has_args=False,
				)
			)

		application.add_handler(
			telegram.ext.MessageHandler(
				filters=(
					(
						telegram.ext.filters.TEXT
						| telegram.ext.filters.CAPTION
					)
					& (~telegram.ext.filters.COMMAND)
					& telegram.ext.filters.USER
					& telegram.ext.filters.CHAT
					& (~telegram.ext.filters.VIA_BOT)
					& usernames_whitelist_filter
				),
				callback=self.__bot.handle_translation,
				block=False,
			)
		)

		self.__logger.info('initialize end')


	async def __handle_error(
			self,
			_: typing.Any,
			context: telegram.ext.ContextTypes.DEFAULT_TYPE,
		) -> None:

		self.__logger.error('application error: %s', context.error)


	async def __shutdown_application(
			self,
			_: ApplicationType,
		) -> None:

		assert self.__background_executor is not None

		self.__logger.info('shutdown begin')

		self.__background_executor.shutdown()

		self.__logger.info('shutdown end')
