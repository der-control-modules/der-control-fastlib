"""
VOLTTRON messaging utilities compatibility shim.

Provides Topic class for callable topic template strings.
"""

from string import Formatter, _string


def normtopic(topic):
    """Normalize topic, removing extra slashes and dots."""
    if not topic:
        return topic
    comps = []
    for comp in topic.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            if comps:
                comps.pop()
        else:
            comps.append(comp)
    return "/".join(comps)


class TopicFormatter(Formatter):
    """
    Format topic strings allowing for optional fields.

    TopicFormatter.format() works similar to the standard str.format()
    built-in, except that format strings may contain double forward
    slashes (//) to indicate break points in the string where a valid
    string may be returned if no replaceable fields occur in the
    following component.
    """

    def _vformat(self, format_string, args, kwargs, used_args, recursion_depth, auto_arg_index=0):
        if recursion_depth < 0:
            raise ValueError("maximum string recursion exceeded")
        result = []
        for literal, name, format_spec, conversion in self.parse(format_string):
            # Handle optional conversion specifiers (S, R)
            if conversion in ["S", "R"]:
                optional = True
                conversion = conversion.lower()
            else:
                optional = False

            if literal:
                result.append(literal)
            if name is None:
                continue

            try:
                obj, arg_used = self.get_field(name, args, kwargs)
            except (KeyError, AttributeError) as e:
                # If field is missing, truncate at last double slash
                if literal:
                    try:
                        literal, _ = literal.rsplit("//", 1)
                    except ValueError:
                        pass
                    else:
                        result[-1] = literal
                        if optional:
                            continue
                        break
                raise e

            used_args.add(arg_used)

            # None values remain as template placeholders
            if obj is None:
                obj = "{{{}{}{}{}{}}}".format(
                    name,
                    "!" if conversion else "",
                    conversion or "",
                    ":" if format_spec else "",
                    format_spec or "",
                )
            else:
                obj = self.convert_field(obj, conversion)
                format_spec, auto_arg_index = self._vformat(format_spec, args, kwargs, used_args, recursion_depth - 1)
                obj = self.format_field(obj, format_spec)
            result.append(obj)
        return "".join(result), auto_arg_index

    def check_unused_args(self, used_args, args, kwargs):
        """Check for unused keyword arguments."""
        for name in kwargs:
            if name not in used_args:
                raise ValueError(f"unused keyword argument: {name}")


class Topic(str):
    """
    A callable topic template string.

    Topic objects can be called with keyword arguments to fill in template fields.
    Uses double slashes (//) as break points for optional portions.
    """

    def __init__(self, format_string):
        """Perform minimal validation of names used in format fields."""
        super().__init__()
        for _, name, _, _ in _string.formatter_parser(format_string):
            if name is None:
                continue
            name, _ = _string.formatter_field_name_split(name)
            if isinstance(name, int) or not name:
                raise ValueError("positional format fields are not supported; use named format fields only")
            if name[:1].isdigit():
                raise ValueError(f"invalid format field name: {name}")

    def __call__(self, **kwargs):
        """Format the topic with keyword arguments and normalize."""
        return self.__class__(normtopic(self.vformat(kwargs)))

    def _(self, **kwargs):
        """Format the topic with keyword arguments without normalizing."""
        return self.__class__(self.vformat(kwargs))

    def format(self, **kwargs):
        """Format the topic with keyword arguments."""
        return self.vformat(kwargs)

    def vformat(self, kwargs):
        """Format using TopicFormatter."""
        formatter = TopicFormatter()
        return formatter.vformat(self, (), kwargs)

    def __repr__(self):
        return f"{self.__class__.__name__}({super().__repr__()})"


__all__ = ["normtopic", "Topic", "TopicFormatter"]
