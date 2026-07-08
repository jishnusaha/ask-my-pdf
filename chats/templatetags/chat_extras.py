from django import template
from django.utils.safestring import mark_safe

from chats.services.rag.parser import render_answer

register = template.Library()


@register.filter(name="render_answer")
def render_answer_filter(text):
    """Render an assistant answer (markdown + [Page N] citations) as safe HTML."""
    return mark_safe(render_answer(text or ""))
