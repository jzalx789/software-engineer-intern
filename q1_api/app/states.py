"""All review transitions live here, including terminal rejections."""
TRANSITIONS = {
    ('ocr_done', 1, 'approve'): 'stage1_approved',
    ('ocr_done', 1, 'reject'): 'stage1_rejected',
    ('stage1_approved', 2, 'approve'): 'stage2_approved',
    ('stage1_approved', 2, 'reject'): 'stage2_rejected',
}
STATUSES = ('pending', 'ocr_done', 'ocr_failed', 'stage1_approved',
            'stage1_rejected', 'stage2_approved', 'stage2_rejected', 'exported')


def transition(current, stage, decision):
    target = TRANSITIONS.get((current, stage, decision))
    if target is None:
        raise ValueError(f'Cannot {decision} at stage {stage} from {current}')
    return target
