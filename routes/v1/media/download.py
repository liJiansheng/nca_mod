from flask import Blueprint
from app_utils import *
import logging
import os
import yt_dlp
import tempfile
from werkzeug.utils import secure_filename
import uuid
from services.cloud_storage import upload_file
from services.authentication import authenticate
from services.file_management import download_file
from urllib.parse import quote

v1_media_download_bp = Blueprint('v1_media_download', __name__)
logger = logging.getLogger(__name__)

@v1_media_download_bp.route('/v1/BETA/media/download', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "media_url": {"type": "string", "format": "uri"},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
        "format": {
            "type": "object",
            "properties": {
                "quality": {"type": "string"},
                "format_id": {"type": "string"},
                "resolution": {"type": "string"},
                "video_codec": {"type": "string"},
                "audio_codec": {"type": "string"}
            }
        },
        "audio": {
            "type": "object",
            "properties": {
                "extract": {"type": "boolean"},
                "format": {"type": "string"},
                "quality": {"type": "string"}
            }
        },
        "thumbnails": {
            "type": "object",
            "properties": {
                "download": {"type": "boolean"},
                "download_all": {"type": "boolean"},
                "formats": {"type": "array", "items": {"type": "string"}},
                "convert": {"type": "boolean"},
                "embed_in_audio": {"type": "boolean"}
            }
        },
        "subtitles": {
            "type": "object",
            "properties": {
                "download": {"type": "boolean"},
                "languages": {"type": "array", "items": {"type": "string"}},
                "formats": {"type": "array", "items": {"type": "string"}}
            }
        },
        "download": {
            "type": "object",
            "properties": {
                "max_filesize": {"type": "integer"},
                "rate_limit": {"type": "string"},
                "retries": {"type": "integer"}
            }
        }
    },
    "required": ["media_url"],
    "additionalProperties": False
})
@queue_task_wrapper(bypass_queue=False)
def download_media(job_id, data):
    media_url = data['media_url']

    format_options = data.get('format', {})
    audio_options = data.get('audio', {})
    thumbnail_options = data.get('thumbnails', {})
    subtitle_options = data.get('subtitles', {})
    download_options = data.get('download', {})

    logger.info(f"Job {job_id}: Received download request for {media_url}")

    try:
        # Create a temporary directory for downloads
        with tempfile.TemporaryDirectory() as temp_dir:
            # Configure yt-dlp options
            ydl_opts = {
                'format': 'best',  # Download best quality
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
            }

            # Add format options if specified
            if format_options:
                format_str = []
                if format_options.get('quality'):
                    format_str.append(format_options['quality'])
                if format_options.get('format_id'):
                    format_str.append(format_options['format_id'])
                if format_options.get('resolution'):
                    format_str.append(format_options['resolution'])
                if format_options.get('video_codec'):
                    format_str.append(format_options['video_codec'])
                if format_options.get('audio_codec'):
                    format_str.append(format_options['audio_codec'])
                if format_str:
                    ydl_opts['format'] = '+'.join(format_str)

            # Add audio options if specified
            if audio_options:
                if audio_options.get('extract'):
                    ydl_opts['extract_audio'] = True
                    if audio_options.get('format'):
                        ydl_opts['audio_format'] = audio_options['format']
                    if audio_options.get('quality'):
                        ydl_opts['audio_quality'] = audio_options['quality']

            # Add thumbnail options if specified
            if thumbnail_options:
                ydl_opts['writesubtitles'] = thumbnail_options.get('download', False)
                ydl_opts['writeallsubtitles'] = thumbnail_options.get('download_all', False)
                if thumbnail_options.get('formats'):
                    ydl_opts['subtitleslangs'] = thumbnail_options['formats']
                ydl_opts['convert_thumbnails'] = thumbnail_options.get('convert', False)
                ydl_opts['embed_thumbnail_in_audio'] = thumbnail_options.get('embed_in_audio', False)

            # Add subtitle options if specified
            if subtitle_options:
                ydl_opts['writesubtitles'] = subtitle_options.get('download', False)
                if subtitle_options.get('languages'):
                    ydl_opts['subtitleslangs'] = subtitle_options['languages']
                if subtitle_options.get('formats'):
                    ydl_opts['subtitlesformat'] = subtitle_options['formats']

            # Add download options if specified
            if download_options:
                if download_options.get('max_filesize'):
                    ydl_opts['max_filesize'] = download_options['max_filesize']
                if download_options.get('rate_limit'):
                    ydl_opts['limit_rate'] = download_options['rate_limit']
                if download_options.get('retries'):
                    ydl_opts['retries'] = download_options['retries']

            # Download the media
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(media_url, download=True)
                filename = ydl.prepare_filename(info)
                
                # Upload to cloud storage
                cloud_url = upload_file(filename)
                
                # Clean up the temporary file
                os.remove(filename)

                # Prepare response
                response = {
                    "media": {
                        "media_url": cloud_url,
                        "title": info.get('title'),
                        "format_id": info.get('format_id'),
                        "ext": info.get('ext'),
                        "resolution": info.get('resolution'),
                        "filesize": info.get('filesize'),
                        "width": info.get('width'),
                        "height": info.get('height'),
                        "fps": info.get('fps'),
                        "video_codec": info.get('vcodec'),
                        "audio_codec": info.get('acodec'),
                        "upload_date": info.get('upload_date'),
                        "duration": info.get('duration'),
                        "view_count": info.get('view_count'),
                        "uploader": info.get('uploader'),
                        "uploader_id": info.get('uploader_id'),
                        "description": info.get('description')
                    }
                }

                # Add thumbnails if available and requested
                if info.get('thumbnails') and thumbnail_options.get('download', False):
                    response["thumbnails"] = []
                    for thumbnail in info['thumbnails']:
                        if thumbnail.get('url'):
                            try:
                                # Download the thumbnail first
                                thumbnail_path = download_file(thumbnail['url'], temp_dir)
                                # Upload to cloud storage
                                thumbnail_url = upload_file(thumbnail_path)
                                # Clean up the temporary thumbnail file
                                os.remove(thumbnail_path)
                                
                                response["thumbnails"].append({
                                    "id": thumbnail.get('id', 'default'),
                                    "image_url": thumbnail_url,
                                    "width": thumbnail.get('width'),
                                    "height": thumbnail.get('height'),
                                    "original_format": thumbnail.get('ext'),
                                    "converted": thumbnail.get('converted', False)
                                })
                            except Exception as e:
                                logger.error(f"Error processing thumbnail: {str(e)}")
                                continue
                
                return response, "/v1/media/download", 200

    except Exception as e:
        logger.error(f"Job {job_id}: Error during download process - {str(e)}")
        return str(e), "/v1/media/download", 500 