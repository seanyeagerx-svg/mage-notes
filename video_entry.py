"""No private workflow prompts or automatic video entry in the public edition."""
START_PHRASES=()
STORY_INSTRUCTION=''
STORY_STAGE='unavailable'
def starts_video_discussion(*args,**kwargs):return False
