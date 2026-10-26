"""Reject excessive multipart uploads before FastAPI spools the whole body."""
from fastapi.routing import APIRoute
from contextlib import asynccontextmanager
from tempfile import TemporaryFile
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser

from app.core.config import get_settings
from app.core.errors import AppError
from app.services.backup_io import require_space, private_temp


class _DataVolumeParser(MultiPartParser):
    def __init__(self,*args,directory,**kwargs):
        super().__init__(*args,**kwargs)
        self.directory=directory

    def on_headers_finished(self):
        super().on_headers_finished()
        upload=self._current_part.file
        if upload is not None:
            # The base parser has only created an EMPTY spool at this point.
            # Replace it before the first file byte arrives, without changing
            # multipart boundary/field/file-count validation.
            old=upload.file
            data=TemporaryFile(mode='w+b',dir=self.directory)
            self._files_to_close_on_error.remove(old)
            old.close()
            self._files_to_close_on_error.append(data)
            self._current_part.file=UploadFile(data,size=0,filename=upload.filename,headers=upload.headers)


@asynccontextmanager
async def backup_form(request,settings):
    with private_temp(settings) as directory:
        parser=_DataVolumeParser(request.headers,request.stream(),directory=directory,max_files=1,max_fields=4,max_part_size=8192)
        try:
            try: form=await parser.parse()
            except MultiPartException as exc:
                raise HTTPException(400,str(exc)) from exc
            try: yield form
            finally: await form.close()
        finally:
            # Also cover disconnects, cancellation and ENOSPC, for which the
            # upstream parser does not run its MultiPartException cleanup.
            for handle in parser._files_to_close_on_error:
                handle.close()


class BackupUploadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            if not self.path.endswith('/restore') or request.method != 'POST':
                return await original(request)
            provider = request.app.dependency_overrides.get(get_settings, get_settings)
            settings = provider()
            limit = settings.max_backup_bytes + 64 * 1024  # bounded form fields/framing
            declared = request.headers.get('content-length')
            if declared is not None and (not declared.isascii() or not declared.isdigit() or len(declared) > 24 or int(declared) > limit):
                raise AppError(413, 'site_backup_too_large', '备份上传超过配置上限')
            directory = settings.storage_root.resolve().parent
            require_space(directory, int(declared or 0) * 3)
            received, checked, failure = 0, 0, None
            receive = request._receive

            async def bounded_receive():
                nonlocal received, checked, failure
                message = await receive()
                if message['type'] == 'http.request':
                    received += len(message.get('body', b''))
                    try:
                        if received > limit:
                            raise AppError(413, 'site_backup_too_large', '备份上传超过配置上限')
                        if received - checked >= 64 * 1024 * 1024:
                            require_space(directory, 64 * 1024 * 1024)
                            checked = received
                    except AppError as exc:
                        failure = exc
                        # Starlette closes all partial spools for this exception.
                        raise MultiPartException('Backup upload quota exceeded') from exc
                return message

            request._receive = bounded_receive
            try:
                return await original(request)
            except HTTPException:
                if failure:
                    raise failure
                raise
            finally:
                request._receive = receive
                await request.close()

        return handle
