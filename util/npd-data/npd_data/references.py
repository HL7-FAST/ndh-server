"""Recursive walk over every Reference-valued node in a resource,
including references carried inside extensions.
"""


def iter_reference_values(resource):
    stack = [resource]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            ref = node.get("reference")
            if isinstance(ref, str):
                yield ref
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
