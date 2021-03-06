"""Parse FGD files, used to describe Hammer entities."""
import itertools
from enum import Enum
from struct import Struct
import io
import math

from typing import (
    Optional, Union, overload,
    TypeVar, Callable, Type,
    Dict, Tuple, List, Set, FrozenSet,
    Mapping, Iterator, Iterable, Collection,
    BinaryIO, TextIO,
    Container,
)

from srctools.filesys import FileSystem, File
from srctools.tokenizer import Tokenizer, Token, TokenSyntaxError

__all__ = [
    'ValueTypes', 'EntityTypes', 'HelperTypes',
    'FGD', 'EntityDef', 'KeyValues',
]

_fmt_8bit = Struct('>B')
_fmt_16bit = Struct('>H')
_fmt_32bit = Struct('>I')
_fmt_double = Struct('>d')
_fmt_header = Struct('>BddI')
_fmt_ent_header = Struct('<BBBBB')


def _read_struct(fmt: Struct, file: BinaryIO) -> tuple:
    return fmt.unpack(file.read(fmt.size))

# Version number for the format.
BIN_FORMAT_VERSION = 2


class FGDParseError(TokenSyntaxError):
    """Raised if the FGD contains invalid syntax."""


class ValueTypes(Enum):
    """Types which can be applied to a KeyValue."""
    # Special cases:
    VOID = 'void'  # Nothing
    CHOICES = 'choices'  # Special - preset value list as string
    SPAWNFLAGS = 'flags'  # Binary flag values.

    # Simple values
    STRING = 'string'
    BOOL = 'boolean'
    INT = 'integer'
    FLOAT = 'float'
    VEC = 'vector'  # Offset or the like
    ANGLES = 'angle'  # Rotation

    # String targetname values (need fixups)
    TARG_DEST = 'target_destination'  # A targetname of another ent.
    TARG_DEST_CLASS = 'target_name_or_class'  # Above + classnames.
    TARG_SOURCE = 'target_source'  # The 'targetname' keyvalue.
    TARG_NPC_CLASS = 'npcclass'  # targetnames filtered to NPC ents
    TARG_POINT_CLASS = 'pointentityclass'  # targetnames filtered to point entities.
    TARG_FILTER_NAME = 'filterclass'  # targetnames of filters.
    TARG_NODE_DEST = 'node_dest'  # name of a node
    TARG_NODE_SOURCE = 'node_id'  # name of us

    # Strings, don't need fixups
    STR_SCENE = 'scene'  # VCD files
    STR_SOUND = 'sound'  # WAV & SoundScript
    STR_PARTICLE = 'particlesystem'  # Particles
    STR_SPRITE = 'sprite'  # Sprite materials
    STR_DECAL = 'decal'  # Sprite materials
    STR_MATERIAL = 'material'  # Materials
    STR_MODEL = 'studio'  # Model
    STR_VSCRIPT = 'scriptlist'  # List of vscripts
    STR_VSCRIPT_SINGLE = 'script'  # Single VScript path.

    # More complex
    ANGLE_NEG_PITCH = 'angle_negative_pitch'  # Inverse pitch of 'angles'
    VEC_LINE = 'vecline'  # Absolute vector, with line drawn from origin to point
    VEC_ORIGIN = 'origin'  # Used for 'origin' keyvalue
    VEC_AXIS = 'axis'
    COLOR_1 = 'color1'  # RGB 0-1 + extra
    COLOR_255 = 'color255'  # RGB 0-255 + extra
    SIDE_LIST = 'sidelist'  # Space-seperated list of sides.

    # Instances
    INST_FILE = 'instance_file'  # File of func_instance
    INST_VAR_DEF = 'instance_parm'  # $fixup definition
    INST_VAR_REP = 'instance_variable'  # $fixup usage

    @property
    def has_list(self) -> bool:
        """Is this a flag or choices value, and needs a [] list?"""
        return self.value in ('choices', 'flags')

    @property
    def is_literal(self) -> bool:
        """Should values of this type be written without quotes around it?"""
        return self.value in ('boolean', 'integer', 'float')

VALUE_TYPE_LOOKUP = {
    typ.value: typ
    for typ in ValueTypes
}
# These have two names pointing to the same type...
VALUE_TYPE_LOOKUP['bool'] = ValueTypes.BOOL
VALUE_TYPE_LOOKUP['int'] = ValueTypes.INT


class EntityTypes(Enum):
    BASE = 'baseclass'  # Not an entity, others inherit from this.
    POINT = 'pointclass'  # Point entity
    BRUSH = 'solidclass'  # Brush entity. Can't have 'model'
    ROPES = 'keyframeclass'  # Used for move_rope etc
    TRACK = 'moveclass'  # Used for path_track etc
    FILTER = 'filterclass'  # Used for filters
    NPC = 'npcclass'  # An NPC


class HelperTypes(Enum):
    """Types of functions in the entity header."""
    INHERIT = 'base'

    # Snap to 1/2 of grid.
    # Special - no arguments.
    HALF_GRID_SNAP = 'halfgridsnap'

    # Simple helpers
    CUBE = 'size'  # Sets size of purple cube
    BBOX = 'bbox'  # Sets bounding box of entity
    TINT = 'color'
    SPHERE = 'sphere'
    LINE = 'line'
    FRUSTUM = 'frustum'
    CYLINDER = 'cylinder'
    BRUSH_SIDES = 'sidelist'  # Highlights brush faces.
    BOUNDING_BOX_HELPER = 'wirebox'  # Displays bounding box from two keyvalues
    # Draws the movement of a player-sized bounding box from A to B.
    SWEPT_HULL = 'sweptplayerhull'

    # Complex helpers using resources
    SPRITE = 'iconsprite'
    MODEL = 'studio'
    MODEL_PROP = 'studioprop'
    MODEL_NEG_PITCH = 'lightprop'  # Uses separate pitch keyvalue

    # Specialty for certain ents
    ENT_SPRITE = 'sprite'
    ENT_INSTANCE = 'instance'
    ENT_DECAL = 'decal'
    ENT_OVERLAY = 'overlay'
    ENT_OVERLAY_WATER = 'overlay_transition'
    ENT_LIGHT = 'light'
    ENT_LIGHT_CONE = 'lightcone'
    ENT_ROPE = 'keyframe'
    ENT_TRACK = 'animator'
    ENT_BREAKABLE_SURF = 'quadbounds'  # Sets the 4 corners on save
    ENT_WORLDTEXT = 'worldtext'  # Renders 3D text in-world.

    # Format extensions.

    # Indicates this entity is only available in the given games.
    EXT_APPLIES_TO = 'appliesto'
    EXT_ORDERBY = 'orderby'  # Reorder keyvalues. Args = names in order.

    
# Ordered list of value types, for encoding in the binary
# format. All must be here, new ones should be added at the end.
VALUE_TYPE_ORDER = [
    ValueTypes.VOID,
    ValueTypes.CHOICES,
    ValueTypes.SPAWNFLAGS,

    ValueTypes.STRING,
    ValueTypes.BOOL,
    ValueTypes.INT,
    ValueTypes.FLOAT,
    ValueTypes.VEC,
    ValueTypes.ANGLES,

    ValueTypes.TARG_DEST,
    ValueTypes.TARG_DEST_CLASS,
    ValueTypes.TARG_SOURCE,
    ValueTypes.TARG_NPC_CLASS,
    ValueTypes.TARG_POINT_CLASS,
    ValueTypes.TARG_FILTER_NAME,
    ValueTypes.TARG_NODE_DEST,
    ValueTypes.TARG_NODE_SOURCE,

    # Strings, don't need fixups
    ValueTypes.STR_SCENE,
    ValueTypes.STR_SOUND,
    ValueTypes.STR_PARTICLE,
    ValueTypes.STR_SPRITE,
    ValueTypes.STR_DECAL,
    ValueTypes.STR_MATERIAL,
    ValueTypes.STR_MODEL,
    ValueTypes.STR_VSCRIPT,

    ValueTypes.ANGLE_NEG_PITCH,
    ValueTypes.VEC_LINE,
    ValueTypes.VEC_ORIGIN,
    ValueTypes.VEC_AXIS,
    ValueTypes.COLOR_1,
    ValueTypes.COLOR_255,
    ValueTypes.SIDE_LIST,

    ValueTypes.INST_FILE,
    ValueTypes.INST_VAR_DEF,
    ValueTypes.INST_VAR_REP,

    ValueTypes.STR_VSCRIPT_SINGLE,
]

# Ditto for entity types.
ENTITY_TYPE_ORDER = [
    EntityTypes.BASE,
    EntityTypes.POINT,
    EntityTypes.BRUSH,
    EntityTypes.ROPES,
    EntityTypes.TRACK,
    EntityTypes.FILTER,
    EntityTypes.NPC,
]

assert set(VALUE_TYPE_ORDER) == set(ValueTypes), \
    "Missing values: " + repr(set(ValueTypes) - set(VALUE_TYPE_ORDER))
assert set(ENTITY_TYPE_ORDER) == set(EntityTypes), \
    "Missing values: " + repr(set(EntityTypes) - set(ENTITY_TYPE_ORDER))
    
# Can only store this many in the bytes.
assert len(VALUE_TYPE_ORDER) < 127, "Too many values."
assert len(ENTITY_TYPE_ORDER) < 255, "Too many entity types."
    
VALUE_TYPE_INDEX = {val: ind for (ind, val) in enumerate(VALUE_TYPE_ORDER)}
ENTITY_TYPE_INDEX = {ent: ind for (ind, ent) in enumerate(ENTITY_TYPE_ORDER)}


def read_colon_list(tok: Tokenizer, had_colon=False) -> Tuple[List[str], Token]:
    """Read strings seperated by colons, up to the end of the line.
    
    The token found at the end is returned.
    """
    strings = []
    ready_for_string = had_colon  # Did we have a colon before?
    token = Token.EOF
    for token, tok_value in tok:
        if token is Token.STRING:
            if not ready_for_string:
                raise tok.error('Too many strings ({!r})!', tok_value)
            strings.append(tok_value)
            ready_for_string = False
        elif token is Token.COLON:
            if ready_for_string:
                # ': :' means to have an empty string there.
                strings.append('')
            ready_for_string = True
        elif token is Token.PLUS:
            if ready_for_string or not strings:
                raise tok.error('"+" without a string before it!')
            strings[-1] += tok.expect(Token.STRING)
        elif ready_for_string and token is Token.NEWLINE:
            continue  # skip over this in particular..
        else:
            if ready_for_string:
                raise tok.error(token)
            return strings, token
    else:
        raise tok.error(token)


def read_tags(tok: Tokenizer) -> FrozenSet[str]:
    """Parse a set of tags from the file.

    The open bracket was just read.
    """
    tags = []
    # Read tags.
    while True:
        token, value = tok()
        if token is Token.STRING:
            tags.append(value.casefold().rstrip(','))
        elif token is Token.BRACK_CLOSE:
            break
        elif token is Token.EOF:
            raise tok.error('Unclosed tags!')
        else:
            raise tok.error(token)
    return validate_tags(tags, tok.error)


def validate_tags(
    tags: Collection[str],
    error: Callable[[str], BaseException]=ValueError,
) -> FrozenSet[str]:
    """Check these tags have valid values.
    
    The error exception is raised if invalid.
    """
    temp_set = {
        t.lstrip('!-+').upper()
        for t in tags
    }
    if len(temp_set) != len(tags):
        raise error('Duplicate tags!')
    if '<any>' in temp_set:
        raise error('<any> cannot be used as a tag!')
    return frozenset({
        t.upper()
        for t in tags
    })
    
    
def match_tags(search: Container[str], tags: Iterable[str]):
    """Check if the search constraints satisfy tags.
    
    The search tags should be uppercased.

    All !tags or -tags cannot be present, all +tags must be present, and
    at lest one normal tag must be present (if they are) to pass.
    """
    if not tags: 
        return True
        
    has_all = '<ALL>' in search
    # None = no normal tags, True = matched one, False = not matched one.
    matched = None
    for tag in tags:
        tag = tag.upper()
        start = tag[0:1]
        if start == '!' or start == '-':
            if tag[1:] in search:
                return False
        elif start == '+':
            if tag[1:] not in search:
                return False
        else:
            if matched is None:
                matched = False
            if has_all or tag in search:
                matched = True

    return matched is not False


class BinStrDict:
    """Manages a "dictionary" for compressing repeated strings in the binary format.
    
    Each unique string is assigned a 2-byte index into the list.
    """
    
    def __init__(self) -> None:
        self._dict = {}
        self.cur_index = 0
        
    def __call__(self, string: str) -> bytes:
        """Get the index for a string. 
        
        If not already present it is assigned one.
        The result is the two bytes that represent the string.
        """
        try:
            index = self._dict[string]
        except KeyError:
            index = self._dict[string] = self.cur_index
            self.cur_index += 1
            # Check it can actually fit.
            if index > (1 << 16):
                raise ValueError("Too many items in dictionary!")
                
        return _fmt_16bit.pack(index)
        
    def serialise(self, file: BinaryIO) -> None:
        """Convert this to a stream of bytes."""
        inv_list = [''] * len(self._dict)
        for txt, ind in self._dict.items():
            inv_list[ind] = txt

        file.write(_fmt_32bit.pack(len(inv_list)))
        for txt in inv_list:
            file.write(_fmt_16bit.pack(len(txt)))
            file.write(txt.encode('utf8'))

    @staticmethod       
    def unserialise(file: BinaryIO) -> Callable[[], str]:
        """Read the dictionary from a file.
        
        This returns a function which reads
        a string from a file at the current point. 
        """
        [length] = _read_struct(_fmt_32bit, file)
        inv_list = [''] * length
        for ind in range(length):
            [str_len] = _read_struct(_fmt_16bit, file)
            inv_list[ind] = file.read(str_len).decode('utf8')

        def lookup() -> str:
            """Read the index from the file, and return the string it matches."""
            [index] = _read_struct(_fmt_16bit, file)
            return inv_list[index]
        
        return lookup

    @staticmethod
    def read_tags(file: BinaryIO, from_dict: Callable[[], str]) -> FrozenSet[str]:
        """Pull tags from a BinStrDict."""
        [size] = _fmt_8bit.unpack(file.read(1))
        return frozenset({
            from_dict()
            for _ in range(size)
        })

    @staticmethod
    def write_tags(
        file: BinaryIO,
        dic: 'BinStrDict',
        tags: Collection[str],
    ) -> None:
        """Write tags a file using the dictionary."""
        file.write(_fmt_8bit.pack(len(tags)))
        for tag in tags:
            file.write(dic(tag))


class KeyValues:
    """Represents a generic keyvalue type.

    If the type is choices or spawnflags, val_list is required:
    * For choices it's a list of (value, name, tags) tuples.
    * For spawnflags it's a list of (bitflag, name, default, tags) tuples.
    """
    def __init__(
        self,
        name: str,
        val_type: ValueTypes,
        disp_name: str,
        default: str,
        doc: str,
        val_list: Union[
            None,
            List[Tuple[int, str, bool, FrozenSet[str]]],
            List[Tuple[str, str, FrozenSet[str]]],
        ],
        is_readonly: bool,
    ):
        self.name = name
        self.type = val_type
        self.default = default
        self.disp_name = disp_name
        self.desc = doc
        self.val_list = val_list
        self.readonly = is_readonly

    def __repr__(self) -> str:
        return (
            'KeyValues({s.name!r}, {s.type!r}, '
            '{s.disp_name!r}, {s.default!r}, '
            '{s.desc!r}, {s.val_list!r}, '
            '{s.readonly})'.format(s=self)
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, KeyValues):
            return (
                self.name == other.name
                and self.type == other.type
                and self.disp_name == other.disp_name
                and self.default == other.default
                and self.desc == other.desc
                and self.val_list == other.val_list
                and self.readonly == other.readonly
            )
        return NotImplemented

    def copy(self) -> 'KeyValues':
        """Create a duplicate of this keyvalue."""
        return KeyValues(
            self.name,
            self.type,
            self.disp_name,
            self.default,
            self.desc,
            self.val_list.copy() if self.val_list else None,
            self.readonly,
        )

    def export(self, file: TextIO, tags: Collection[str]=()) -> None:
        """Write this back out to a FGD file."""
        file.write('\t' + self.name)
        if tags:
            file.write('[' + ', '.join(tags) + ']')
        file.write('({}) '.format(self.type.value))

        if self.readonly:
            file.write('readonly ')

        file.write(': "{}"'.format(self.disp_name))
        if self.default:
            if self.type.is_literal:  # Int/float etc.
                file.write(' : {}'.format(self.default))
            else:
                file.write(' : "{}"'.format(self.default))
            if self.desc:
                file.write(' : "{}"'.format(self.desc.replace('\n', '\\n')))
        else:
            if self.desc:
                file.write(' : : "{}"'.format(self.desc.replace('\n', '\\n')))

        if self.type.has_list:
            file.write(' =\n\t\t[\n')
            if self.type is ValueTypes.SPAWNFLAGS:
                # Empty tuple handles a None value.
                for index, name, default, tags in self.val_list or ():
                    file.write('\t\t{}: "{}" : {}'.format(
                        index,
                        name,
                        int(default),
                    ))
                    if tags:
                        file.write(' [' + ', '.join(tags) + ']\n')
                    else:
                        file.write('\n')
            elif self.type is ValueTypes.CHOICES:
                for value, name, tags in self.val_list or ():
                    # Numbers can be unquoted, everything else cannot.
                    try:
                        float(value)
                    except ValueError:
                        value = '"' + value + '"'

                    file.write('\t\t{}: "{}"'.format(value, name))
                    if tags:
                        file.write(' [' + ', '.join(tags) + ']\n')
                    else:
                        file.write('\n')
            else:
                raise AssertionError('No other types possible!')
            file.write('\t\t]\n')

        file.write('\n')

    def serialise(self, file, str_dict: BinStrDict):
        """Write to the binary file."""
        file.write(str_dict(self.name))
        file.write(str_dict(self.disp_name))
        value_type = VALUE_TYPE_INDEX[self.type]
        # Use the high bit to store this inside here as well.
        if self.readonly:
            value_type |= 128
        file.write(_fmt_8bit.pack(value_type))
        
        # Spawnflags have integer names and defaults,
        # choices has string values and no default.
        if self.type is ValueTypes.SPAWNFLAGS:
            file.write(_fmt_8bit.pack(len(self.val_list)))
            # spawnflags go up to at least 1<<23.
            for val, name, default in self.val_list:
                # We can write 2^n instead of the full number,
                # since they're all powers of two.
                power = int(math.log2(val))
                if default:  # Pack the default as the MSB.
                    power |= 128
                file.write(_fmt_8bit.pack(power))
                file.write(str_dict(name))
            return  # Spawnflags doesn't need to write a default.
        
        file.write(str_dict(self.default or ''))
        
        if self.type is ValueTypes.CHOICES:
            # Use two bytes, these can be large (soundscapes).
            file.write(_fmt_16bit.pack(len(self.val_list)))
            for val, name in self.val_list:
                file.write(str_dict(val))
                file.write(str_dict(name))
        
    @staticmethod
    def unserialise(
        file: BinaryIO,
        from_dict: Callable[[], str],
    ) -> 'KeyValues':
        """Recover a KeyValue from a binary file."""
        name = from_dict()
        disp_name = from_dict()
        [value_ind] = _read_struct(_fmt_8bit, file)
        readonly = value_ind & 128
        value_type = VALUE_TYPE_ORDER[value_ind & 127]
        
        val_list = None
        
        if value_type is ValueTypes.SPAWNFLAGS:
            default = ''  # No default for this type.
            [val_count] = _read_struct(_fmt_8bit, file)
            val_list = [0] * val_count
            for ind in range(val_count):
                [power] = _read_struct(_fmt_8bit, file)
                val_name = from_dict()
                val_list[ind] = (1 << (power & 127), val_name, (power & 128) != 0)
        else:
            default = from_dict()
            
            if value_type is ValueTypes.CHOICES:
                [val_count] = _read_struct(_fmt_16bit, file)
                val_list = [0] * val_count
                for ind in range(val_count):
                    val_list[ind] = (from_dict(), from_dict())
        
        return KeyValues(
            name,
            value_type,
            disp_name,
            default,
            '',
            val_list,
            readonly,
        )


class IODef:
    """Represents an input or output for an entity."""
    def __init__(self, name, val_type: ValueTypes, description: str=''):
        self.name = name
        self.type = val_type
        self.desc = description
        
    def __repr__(self) -> str:
        txt = '{}({!r}, {!r}'.format(
            self.__class__.__name__,
            self.name,
            self.type,
        )
        if self.desc:
            txt += ', ' + repr(self.desc)
        return txt + ')'

    def copy(self) -> 'IODef':
        """Create a duplicate of this IODef."""
        return IODef(self.name, self.type, self.desc)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, IODef):
            return (
                self.name == other.name
                and self.type == other.type
                and self.desc == other.desc
            )
        return NotImplemented

    def export(
        self,
        file: TextIO,
        io_type: str,
        tags: Collection[str]=(),
    ) -> None:
        """Write this back out to a FGD file.

        io_type should be "input" or "output".
        """
        file.write('\t{} {}'.format(
            io_type,
            self.name,
        ))

        if tags:
            file.write('[' + ', '.join(tags) + ']')

        file.write('({})'.format(self.type.value))

        if self.desc:
            file.write(' : "{}"\n'.format(self.desc))
        else:
            file.write('\n')
        
    def serialise(self, file: BinaryIO, dic: BinStrDict) -> None:
        """Write to the binary file."""
        file.write(dic(self.name))
        file.write(_fmt_8bit.pack(VALUE_TYPE_INDEX[self.type]))

    @staticmethod
    def unserialise(
        file: BinaryIO,
        from_dict: Callable[[], str],
    ) -> 'IODef':
        """Recover an IODef from a binary file."""
        name = from_dict()
        value_type = VALUE_TYPE_ORDER[_read_struct(_fmt_8bit, file)[0]]
        return IODef(name, value_type)


T = TypeVar('T')


class _EntityView(Mapping[Union[str, Tuple[str, Iterable[str]]], T]):
    """Provides a view over entity keyvalues, inputs, or outputs."""
    __slots__ = ['_ent', '_attr', '_disp_attr',]

    # Note, we expect the maps to have casefolded their keys.

    def __init__(self, ent: 'EntityDef', attr_name: str, disp_name: str):
        self._ent = ent
        self._attr = attr_name
        self._disp_attr = disp_name
        
    @property
    def __name__(self) -> str:
        return self._disp_attr
        
    def __repr__(self) -> str:
        return '{!r}.{}'.format(self._ent, self._disp_attr)

    def __eq__(self, other) -> bool:
        """We're private, so we should be the only instance for a given Entity."""
        return other is self
        
    def _maps(self, ent=None) -> Iterator[Mapping[str, T]]:
        """Yield all the mappings which we need to look through."""
        if ent is None:
            ent = self._ent

        yield getattr(ent, self._attr)
        for base in ent.bases:
            yield from self._maps(base)

    def __getitem__(self, name: Union[str, Tuple[str, Iterable[str]]]) -> T:
        """Lookup the value in the entity.

        Either obj['name'], or obj['name', {tags}] is accepted.
        """
        if isinstance(name, str):
            search_tags = set()
        elif isinstance(name, tuple):
            name, search_tags = name
            search_tags = frozenset({t.casefold() for t in search_tags})
        else:
            raise TypeError(
                'Expected str or (str, Iterable[str]), '
                'got "{}"'.format(name),
            )
        name = name.casefold()
        for ent_map in self._maps():
            try:
                tag_map = ent_map[name]
            except KeyError:
                continue
            
            # Force longer more-specific tags to match first.
            for tags, value in sorted(
                tag_map.items(),
                key=lambda t: len(t[0]),
                reverse=True,
            ):
                if match_tags(search_tags, tags):
                    return value
        raise KeyError((name, search_tags))
        
    def __iter__(self) -> Iterator[T]:
        """Yields all keys this object has."""
        seen = set()
        for ent_map in self._maps():
            for name in ent_map:
                if name in seen:
                    continue
                seen.add(name)
                yield name
            
    def __len__(self) -> int:
        seen = set()  # type: Set[str]
        for ent_map in self._maps():
            seen.update(ent_map)
        return len(seen)


# Fix a bug in some typing versions - slots can't be used with generics.
del _EntityView.__slots__

# Cache the classes ourselves here.
_Ent_View_KV = _EntityView[KeyValues]
_Ent_View_IO = _EntityView[IODef]


class EntityDef:
    """A definition for an entity."""
    def __init__(self, typ: EntityTypes) -> None:
        self.type = typ
        self.classname = ''
        # These are (name) -> {tags} -> value dicts.
        self.keyvalues = {}  # type: Dict[str, Dict[FrozenSet[str], KeyValues]]
        self.inputs = {}  # type: Dict[str, Dict[FrozenSet[str], IODef]]
        self.outputs = {}  # type: Dict[str, Dict[FrozenSet[str], IODef]]

        # Keyvalues have an order. If not present in here,
        # they appear at the end.
        self.kv_order = []  # type: List[str]

        # Base type names - base()
        self.bases = []  # type: List[Union[EntityDef, str]]
        # line(), studio(), etc in the header
        # this is a func, args tuple.
        self.helpers = []  # type: List[Tuple[HelperTypes, List[str]]]
        self.desc = ''
        
        # Views for accessing data among all the entities.
        self.kv = _Ent_View_KV(self, 'keyvalues', 'kv')  # type: _EntityView[KeyValues]
        self.inp = _Ent_View_IO(self, 'inputs', 'inp')  # type: _EntityView[IODef]
        self.out = _Ent_View_IO(self, 'outputs', 'out')  # type: _EntityView[IODef]

    @classmethod
    def parse(
        cls,
        fgd: 'FGD',
        tok: Tokenizer,
        ent_type: EntityTypes,
        eval_bases: bool=True,
    ):
        """Parse an entity definition."""
        entity = cls(ent_type)

        # First parse the bases part - lots of name(args) sections until an '='
        help_type = None
        for token, token_value in tok:
            if token is Token.NEWLINE:
                continue
            if token is Token.STRING:
                if help_type is None:
                    try:
                        help_type = HelperTypes(token_value)
                    except ValueError:
                        raise tok.error(
                            'Unknown HelperType "{}"!',
                            token_value,
                        )
                    continue
                else:
                    # No arguments for the previous helper - add it in like that.
                    entity.helpers.append((help_type, ['']))

            elif token is Token.PAREN_ARGS:
                if help_type is None:
                    raise tok.error('Args without helper type! ({!r})', token_value)

                args = [
                    arg.strip()
                    for arg in
                    token_value.split(',')
                ]

                if help_type is HelperTypes.INHERIT:
                    for base in args:
                        if eval_bases:
                            base = fgd[base]
                        if base not in entity.bases:
                            entity.bases.append(base)
                    help_type = None
                    continue

                entity.helpers.append((help_type, args))

                help_type = None

            elif token is Token.EQUALS:
                break
            else:
                raise tok.error(token)
        else:
            raise tok.error('Entity header never ended!')

        # We were waiting for arguments for the previous helper.
        # We need to add with none.
        if help_type:
            entity.helpers.append((help_type, []))

        entity.classname = tok.expect(Token.STRING).strip()

        # We next might have a ':' then docstring before the [,
        # or directly to [.
        desc = None  # type: List[str]
        for doc_token, token_value in tok:
            if doc_token is Token.NEWLINE:
                continue
            if doc_token is Token.COLON:
                if desc is None:
                    desc = []
                else:
                    raise tok.error('Two colons in entity description!')
            elif doc_token is Token.STRING:
                if desc is None or desc:
                    # No colon yet, or we have text without '+' between
                    raise tok.error(doc_token)
                desc.append(token_value)
            elif doc_token is Token.PLUS:
                if not desc:
                    raise tok.error('+ without string before it!')
                desc.append(tok.expect(Token.STRING))
            elif doc_token is Token.BRACK_OPEN:
                if desc:
                    entity.desc = ''.join(desc)
                break
            else:
                raise tok.error(doc_token)

        fgd.entities[entity.classname.casefold()] = entity

        # Now parse keyvalues, and input/outputs
        for token, token_value in tok:
            if token is Token.BRACK_CLOSE:
                break  # End of this entity.

            if token is Token.NEWLINE:
                continue

            # IO - keyword at the start.
            if token is not Token.STRING:
                raise tok.error(token)

            io_type = token_value.casefold()
            if io_type in ('input', 'output'):

                name = tok.expect(Token.STRING)
                
                # Next is either the value type parens, or a tags brackets.
                val_token, raw_value_type = tok()
                if val_token is Token.BRACK_OPEN:
                    tags = read_tags(tok)
                    val_token, raw_value_type = tok()
                else:
                    tags = frozenset()
                    
                raw_value_type = raw_value_type.strip()
                try:
                    val_typ = VALUE_TYPE_LOOKUP[raw_value_type.casefold()]
                except KeyError:
                    raise tok.error('Unknown keyvalue type "{}"!', raw_value_type)

                # Can't have a spawnflags or choices input type...
                if val_typ.has_list:
                    raise tok.error(
                        '"{}" value type is not valid for an input or output!',
                        val_typ.value,
                    )

                # Read desc
                attrs, token = read_colon_list(tok)

                if token is token.EQUALS:
                    raise tok.error(token)

                if attrs:
                    try:
                        [io_desc] = attrs
                    except ValueError:
                        raise tok.error('Too many values for IO definition!')
                else:
                    io_desc = ''

                # entity.inputs or entity.outputs
                tags_map = getattr(entity, io_type + 's').setdefault(name.casefold(), {})
                tags_map[tags] = IODef(name, val_typ, io_desc)

            else:
                # Keyvalue
                name = io_type
                
                # Next is either the value type parens, or a tags brackets.
                
                val_token, raw_value_type = tok()
                if val_token is Token.BRACK_OPEN:
                    tags = read_tags(tok)
                    val_token, raw_value_type = tok()
                else:
                    tags = frozenset()
                
                if val_token is not Token.PAREN_ARGS:
                    raise tok.error(val_token)  

                raw_value_type = raw_value_type.strip()
                try:
                    val_typ = VALUE_TYPE_LOOKUP[raw_value_type.casefold()]
                except KeyError:
                    raise tok.error('Unknown keyvalue type "{}"!', raw_value_type)

                next_token, key_flag = tok()

                is_readonly = False
                had_colon = False
                has_equal = None
                attrs = None

                if next_token is Token.STRING:
                    # 'report' or 'readonly'
                    if key_flag.casefold() == 'readonly':
                        is_readonly = True
                elif next_token is Token.COLON:
                    had_colon = True
                elif next_token is Token.EQUALS:
                    # Special case - spawnflags doesn't have to have
                    # any info - skips straight to the end.
                    if val_typ is ValueTypes.SPAWNFLAGS:
                        attrs = []
                        has_equal = next_token
                elif next_token is Token.NEWLINE:
                    attrs = []
                    has_equal = next_token
                else:
                    raise tok.error(next_token)

                if attrs is None:
                    attrs, has_equal = read_colon_list(tok, had_colon)
                attr_len = len(attrs)

                desc = default = ''
                if attr_len == 3:
                    disp_name, default, desc = attrs
                elif attr_len == 2:
                    disp_name, default = attrs
                elif attr_len == 1:
                    [disp_name] = attrs
                elif attr_len == 0:
                    disp_name = name
                else:
                    raise tok.error('Too many attributes for keyvalue!\n{!r}', attrs)

                if val_typ.has_list:
                    if has_equal is not Token.EQUALS:
                        raise tok.error('No list for "{}" value type!', val_typ.name)
                    # Read the choices in the []
                    val_list = []
                    tok.expect(Token.BRACK_OPEN)
                    for choices_token, choices_value in tok:
                        if choices_token is Token.NEWLINE:
                            continue
                        if choices_token is Token.BRACK_CLOSE:
                            break
                        elif choices_token is not Token.STRING:
                            raise tok.error(choices_token)
                        vals, end_token = read_colon_list(tok, had_colon=False)

                        if end_token is Token.BRACK_OPEN:
                            val_tags = read_tags(tok)
                        else:
                            val_tags = frozenset()
                        
                        if val_typ is ValueTypes.SPAWNFLAGS:
                            # The first value is an integer.
                            try:
                                choices_value = int(choices_value)
                            except ValueError:
                                raise tok.error(
                                    'SpawnFlags must be integer values, '
                                    'not "{}" (in {})!'.format(
                                        choices_value, 
                                        entity.classname, 
                                    )
                                ) from None
                            power = math.log2(choices_value)
                            if power != round(power):
                                raise tok.error(
                                    'SpawnFlags must be powers of two, '
                                    'not {} (in {})!'.format(
                                        choices_value,
                                        entity.classname,
                                    )
                                ) from None

                        # Spawnflags can have a default, others don't
                        if len(vals) == 2 and val_typ is ValueTypes.SPAWNFLAGS:
                            val_list.append((choices_value, vals[0], vals[1].strip() == '1', val_tags))
                        elif len(vals) == 1:
                            if val_typ is ValueTypes.SPAWNFLAGS:
                                val_list.append((choices_value, vals[0], True, val_tags))
                            else:
                                val_list.append((choices_value, vals[0], val_tags))
                        elif len(vals) == 0:
                            raise tok.error(Token.STRING)
                        else:
                            raise tok.error('Too many values!\n{}', vals)

                        # Handle ] at the end of a : : line.
                        if end_token is Token.BRACK_CLOSE:
                            break
                    else:
                        raise tok.error(token.EOF)
                else:
                    val_list = None
                    if has_equal is Token.EQUALS:
                        raise tok.error('"{}" value types can\'t have lists!', val_typ.name)

                tags_map = entity.keyvalues.setdefault(name.casefold(), {})
                if not tags_map:
                    # New, add to the ordering.
                    entity.kv_order.append(name.casefold())

                tags_map[tags] = KeyValues(
                    name,
                    val_typ,
                    disp_name,
                    default,
                    ''.join(desc),
                    val_list,
                    is_readonly == 'readonly',
                )

    def __repr__(self) -> str:
        if self.type is EntityTypes.BASE:
            return '<Entity Base "{}">'.format(self.classname)
        else:
            return '<Entity {}>'.format(self.classname)

    def strip_tags(self, tags: FrozenSet[str]) -> None:
        """Strip all tags from this entity, blanking them.

        Only values matching the given tags will be kept.
        """
        for category in [self.keyvalues, self.inputs, self.outputs]:
            for key, tag_map in list(category.items()):
                # Force longer more-specific tags to match first.
                for key_tag, value in sorted(
                    tag_map.items(),
                    key=lambda t: len(t[0]),
                    reverse=True,
                ):
                    if match_tags(tags, key_tag):
                        category[key] = {
                            frozenset(): value
                        }
                        if isinstance(value, KeyValues) and value.val_list:

                            # Filter the value list as well.
                            value.val_list = [
                                val[:-1] + (frozenset(), )
                                for val in value.val_list
                                if match_tags(tags, val[-1])
                            ]
                        break
                else:
                    del category[key]

    def export(self, file: TextIO) -> None:
        """Write the entity out to a FGD file."""
        # Make it look pretty: BaseClass
        file.write('\n@{} '.format(
            self.type.value.title().replace('class', 'Class')
        ))
        if self.bases:
            file.write('base(')
            file.write(', '.join([
                (base.classname if isinstance(base, EntityDef) else base)
                for base in self.bases
            ]))
            file.write(') ')

        kv_order_list = []

        for helper, args in self.helpers:
            file.write('\n\t{}({}) '.format(helper.value, ', '.join(args)))
            if helper is HelperTypes.EXT_ORDERBY:
                kv_order_list.extend(map(str.casefold, args))

        if self.helpers:
            file.write('\n')  # Put the classname on the following line.
        file.write('= {}'.format(self.classname))

        if self.desc:
            file.write(': "{}"'.format(self.desc.replace('\n', '\\n')))

        file.write('\n\t[\n')

        kv_order = {
            name: ind
            for ind, name in
            enumerate(kv_order_list or self.kv_order)
        }

        for name, kv_map in sorted(
            self.keyvalues.items(),
            # Sort by position in kv_order. If not present add to the end.
            key=lambda name_kv: kv_order.get(name_kv[0], 2**64),
        ):
            for tags, kv in kv_map.items():
                kv.export(file, tags)

        if self.inputs:
            file.write('\n\t// Inputs\n')

            for inp_map in self.inputs.values():
                for tags, inp in inp_map.items():
                    inp.export(file, 'input', tags)

        if self.outputs:
            file.write('\n\t// Outputs\n')

            for out_map in self.outputs.values():
                for tags, out in out_map.items():
                    out.export(file, 'output', tags)
        file.write('\t]\n')

    def iter_bases(self, _done: Set['EntityDef']=None) -> Iterator['EntityDef']:
        """Yield all base entities for this one.

        If an entity is repeated, it will only be yielded once.
        """
        if not _done:
            _done = {self}
        for ent in self.bases:
            _done.add(ent)
            yield ent
            yield from ent.iter_bases(_done)
            
    def serialise(self, file, str_dict: BinStrDict):
        """Write to the binary file."""
        file.write(_fmt_ent_header.pack(
            ENTITY_TYPE_INDEX[self.type],
            len(self.bases),
            len(self.keyvalues),
            len(self.inputs),
            len(self.outputs),
        ))
        file.write(str_dict(self.classname))
        
        for base_ent in self.bases:
            file.write(str_dict(base_ent.classname))
        
        for obj_type in (self.keyvalues, self.inputs, self.outputs):
            for name, tag_map in obj_type.items():
                # We don't need to write the name, since that's stored
                # also in the kv/io object itself.

                if not tag_map:
                    # No need to add this one.
                    continue

                # Special case - if there is one blank tag, write len=0
                # and just the value.
                # That saves 2 bytes.
                if len(tag_map) == 1:
                    [(tags, value)] = tag_map.items()
                    if not tags:
                        file.write(_fmt_8bit.pack(0))
                        value.serialise(file, str_dict)
                        continue

                file.write(_fmt_8bit.pack(len(tag_map)))
                for tags, value in tag_map.items():
                    BinStrDict.write_tags(file, str_dict, tags)
                    value.serialise(file, str_dict)

        # Helpers are not added.
        
    @staticmethod
    def unserialise(
        file: BinaryIO,
        from_dict: Callable[[], str],
    ) -> 'EntityDef':
        """Read from the binary file."""
        [
            type_ind,
            base_count,
            kv_count,
            inp_count,
            out_count,
        ] = _read_struct(_fmt_ent_header, file)  # type: int, int, int, int, int
        
        ent = EntityDef(ENTITY_TYPE_ORDER[type_ind])
        ent.classname = from_dict()
        ent.desc = ''
        
        for _ in range(base_count):
            # We temporarily store strings, then evaluate later on.
            ent.bases.append(from_dict())  # type: ignore

        for count, val_map, cls in [
            (kv_count, ent.keyvalues, KeyValues),
            (inp_count, ent.inputs, IODef),
            (out_count, ent.outputs, IODef),
        ]:  # type: int, Dict[str, Dict[FrozenSet[str], Union[KeyValues, IODef]]], Type[Union[KeyValues, IODef]]
            for _ in range(count):
                [tag_count] = _read_struct(_fmt_8bit, file)
                if tag_count == 0:
                    # Special case, a single untagged item.
                    obj = cls.unserialise(file, from_dict)
                    val_map[obj.name] = {frozenset(): obj}
                else:

                    # We know it's starting empty, and must have at least
                    # one tag.

                    tag = BinStrDict.read_tags(file, from_dict)
                    obj = cls.unserialise(file, from_dict)
                    tag_map = val_map[obj.name] = {tag: obj}
                    for _ in range(tag_count - 1):
                        tag = BinStrDict.read_tags(file, from_dict)
                        obj = cls.unserialise(file, from_dict)
                        tag_map[tag] = obj

        return ent


class FGD:
    """A FGD set for a game. May be composed of several files."""
    def __init__(self) -> None:
        """Create a FGD."""
        # List of names we have already parsed.
        # We don't parse them again, to prevent infinite loops.
        self._parse_list = []
        # Entity definitions
        self.entities = {}  # type: Dict[str, EntityDef]
        # maximum bounding box of map
        self.map_size_min = 0
        self.map_size_max = 0

    @classmethod
    def parse(
        cls,
        file: Union[File, str],
        filesystem: FileSystem=None,
    ) -> 'FGD':
        """Parse an FGD file.

        Parameters:
        * file: A filesys.File representing the file to read, or a file path.
        * filesystem: The system to lookup files in. This is needed to 
          resolve file inclusions. If not passed, file must be a filesystem
          File to obtain a matching filesystem.
        """
        if filesystem is not None and not isinstance(file, File):
            if not file.endswith('.fgd'):
                file += '.fgd'
            try:
                with filesystem:
                    file = filesystem[file]
            except KeyError:
                raise FileNotFoundError(file)
        elif isinstance(file, File):
            filesystem = file.sys
        else:
            raise TypeError(
                'String file path passed ({!r}), but no filesystem!'.format(file)
            )
        fgd = cls()
        fgd.parse_file(filesystem, file)
        return fgd

    def apply_bases(self) -> None:
        """Fix base values in entities after parsing.
        
        While parsing the classnames may be set as strings,
        so order in the file doesn't matter. This fixes
        them to the real entity objects.
        """
        for ent in self:
            orig_bases = ent.bases
            new_bases = ent.bases = []
            for base in orig_bases:
                if isinstance(base, EntityDef):
                    # This entity was already done.
                    new_bases.append(base)
                    continue
                
                try:
                    new_bases.append(self[base])  # type: ignore
                except KeyError:
                    raise ValueError(
                        'Unknown base ({}) for {}'.format(
                            base,
                            ent.classname,
                        )
                    )

    def collapse_bases(self) -> None:
        """Collapse all bases into the entities that use them.

        This operates in-place, and clears all the base attributes as a result.
        """
        # We need to do a topological sort effectively, to ensure we do
        # parents before children.
        todo = set(self)  # type: Set[EntityDef]
        done = set()  # type: Set[EntityDef]
        while todo:
            deferred = set()  # type: Set[EntityDef]
            for ent in todo:
                if not ent.bases:
                    done.add(ent)
                    continue

                ready = True
                for base in ent.bases:
                    if isinstance(base, str):
                        raise ValueError(
                            'Unevaluated base: {} in {}!'.format(
                                base, ent.classname
                            ))
                    if base not in done:
                        deferred.add(base)
                        ready = False
                if not ready:
                    deferred.add(ent)
                    continue
                # All of this entity's bases are collapsed.
                # We can collapse it.

                base_kv = []
                keyvalue_names = set(ent.kv_order)

                for base in ent.bases:
                    for name, base_kv_map in base.keyvalues.items():
                        ent_kv_map = ent.keyvalues.setdefault(name, {})
                        for tag, kv in base_kv_map.items():
                            if tag not in ent_kv_map:
                                ent_kv_map[tag] = kv.copy()
                            elif kv.type.has_list:
                                # If both are lists, merge those. This is mainly
                                # for spawnflags.
                                targ_list = ent_kv_map[tag].val_list
                                if targ_list:
                                    for val in kv.val_list:
                                        if val not in targ_list:
                                            targ_list.append(val)

                        if name not in keyvalue_names:
                            base_kv.append(name)
                            keyvalue_names.add(name)

                    for base_map, ent_map in [
                        (base.inputs, ent.inputs),
                        (base.outputs, ent.outputs),
                    ]:
                        for name, base_tags_map in base_map.items():
                            ent_tags_map = ent_map.setdefault(name, {})
                            for tag, io_def in base_tags_map.items():
                                if tag not in ent_tags_map:
                                    ent_tags_map[tag] = io_def.copy()

                ent.kv_order = base_kv + ent.kv_order
                ent.bases.clear()
                done.add(ent)

            # All the entities have a dependency on another.
            if todo == deferred:
                raise ValueError(
                    "Loop in bases! \n "
                    "Problematic entities: \n{}".format([
                        ent.classname
                        for ent in todo
                    ]))

            todo = deferred

    @overload
    def export(self) -> str: ...
    @overload
    def export(self, file: TextIO) -> None: ...
    def export(self, file=None):
        """Write the FGD contents into a text file.

        If none are provided, the text will be returned.
        """
        ret_string = file is None
        if ret_string:
            file = io.StringIO()

        if self.map_size_min != self.map_size_max:
            file.write('@mapsize({}, {})\n\n'.format(self.map_size_min, self.map_size_max))

        # TODO: topological sort.
        for ent in self.entities.values():
            ent.export(file)

        if ret_string:
            return file.getvalue()

    def parse_file(
        self,
        filesys: FileSystem,
        file: File,
        *,
        eval_bases: bool=True,
        encoding='cp1252',
    ) -> None:
        """Parse one file (recursively if needed).

        If eval_bases is False, bases will not be computed. This makes it
        impossible in some cases to evaluate these later, but it can help
        if it is not required.
        """

        if file in self._parse_list:
            return

        self._parse_list.append(file)

        with filesys, file.open_str(encoding) as f:
            tokeniser = Tokenizer(
                f,
                filename=file.path,
                error=FGDParseError,
                string_bracket=False,
            )
            for token, token_value in tokeniser:
                # The only things at top-level would be bare strings, and empty lines.
                if token is Token.NEWLINE:
                    continue
                if token is not Token.STRING:
                    raise tokeniser.error(token)
                token_value = token_value.casefold()

                if token_value == '@include':
                    include_file = tokeniser.expect(Token.STRING)
                    if not include_file.endswith('.fgd'):
                        include_file += '.fgd'

                    try:
                        include = filesys[include_file]
                    except KeyError:
                        raise FileNotFoundError(file)
                    self.parse_file(
                        filesys,
                        include,
                        eval_bases=eval_bases,
                        encoding=encoding,
                    )

                elif token_value == '@mapsize':
                    # Max/min map size definition
                    mapsize_args = tokeniser.expect(Token.PAREN_ARGS)
                    try:
                        min_size, max_size = mapsize_args.split(',')
                        self.map_size_min = int(min_size.strip())
                        self.map_size_max = int(max_size.strip())
                    except ValueError:
                        raise tokeniser.error(
                            'Invalid @MapSize: ({})',
                            mapsize_args,
                        )
                # Entity definition...
                elif token_value[:1] == '@':
                    try:
                        ent_type = EntityTypes(token_value[1:])
                    except ValueError:
                        raise tokeniser.error(
                            'Invalid Entity type "{}"!',
                            token_value[1:],
                        )
                    EntityDef.parse(self, tokeniser, ent_type, eval_bases)
                else:
                    raise tokeniser.error('Bad keyword {!r}', token_value)

    def __getitem__(self, classname: str) -> EntityDef:
        try:
            return self.entities[classname.casefold()]
        except KeyError:
            raise KeyError('No class "{}"!'.format(classname)) from None

    def __iter__(self) -> Iterator[EntityDef]:
        return iter(self.entities.values())

    def __len__(self) -> int:
        return len(self.entities)

    def _fix_missing_bases(self, ent: EntityDef) -> None:
        """Fix issues that prevent serialising base entities.

        The FGD implementation by Valve is very order-dependent.
        It is possible to have a base class overwritten by a real entity,
        as long as it comes before that in the file. To allow serialising
        this, fix those entities by appending numbers to any not in the list.
        This is run recursively on every entity to check their bases.
        """
        for base in ent.bases:
            # If it's in there, it'll be found again.
            # We've also (or are going to) pass over this one.
            # So don't redo it.
            if self[base.classname] is base:
                continue

            base_name = base.classname.rstrip('_0123456789').casefold() + '_'
            for num in itertools.count(1):
                poss_name = base_name + str(num)
                if poss_name not in self:
                    base.classname = poss_name
                    self.entities[poss_name] = base
                    break
            self._fix_missing_bases(base)

    def serialise(self, file: BinaryIO) -> None:
        """Write the FGD into a compacted binary format.
        
        This is only readable by this module, and does not contain
        entity, keyvalue and IO help descriptions to keep the data small.
        """
        for ent in list(self):
            self._fix_missing_bases(ent)

        # The start of a file is a list of all used strings. 
        dictionary = BinStrDict()
        
        # Start of file - format version, FGD min/max, number of entities.
        file.write(b'FGD' + _fmt_header.pack(
            BIN_FORMAT_VERSION,
            self.map_size_min,
            self.map_size_max,
            len(self.entities),
        ))
        
        ent_data = io.BytesIO()
        for ent in self.entities.values():
            ent.serialise(ent_data, dictionary)
            
        # The final file is the header, dictionary data, and all the entities 
        # one after each other.
        dictionary.serialise(file)
        file.write(ent_data.getvalue())
      
    @classmethod  
    def unserialise(cls, file: BinaryIO) -> 'FGD':
        """Unpack data from FGD.serialise() to return the original data.
        
        Help descriptions are not preserved, and are set to <BINARY>.
        """
        
        if file.read(3) != b'FGD':
            raise ValueError('Not an FGD file!')
        
        fgd = FGD()
        
        [
            format_version,
            fgd.map_size_min,
            fgd.map_size_max,
            ent_count,
        ] = _read_struct(_fmt_header, file)
        
        if format_version != BIN_FORMAT_VERSION:
            raise TypeError('Unknown format version "{}"!'.format(format_version))
            
        from_dict = BinStrDict.unserialise(file)

        # Now there's ent_count entities after each other.
        for _ in range(ent_count):
            ent = EntityDef.unserialise(file, from_dict)
            fgd.entities[ent.classname.casefold()] = ent

        fgd.apply_bases()

        return fgd

