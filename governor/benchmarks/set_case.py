"""Set the starting relaxation factors and the iteration limit of a tutorial copy."""
import re
import sys

case, factor, p_factor, max_iter = sys.argv[1:5]

path = f"{case}/system/fvSolution"
text = open(path).read()
head, relax = text.split("relaxationFactors", 1)
relax = re.sub(r'^(\s+(?:U|nuTilda|k|epsilon|"\.\*")\s+)[\d.]+;.*$', rf"\g<1>{factor};", relax, flags=re.M)
if p_factor != "-":
    relax = re.sub(r"^(\s+p\s+)[\d.]+;.*$", rf"\g<1>{p_factor};", relax, flags=re.M)
open(path, "w").write(head + "relaxationFactors" + relax)

path = f"{case}/system/controlDict"
text = open(path).read()
text = re.sub(r"^endTime\s+\S+;", f"endTime         {max_iter};", text, flags=re.M)
text = re.sub(r"^writeInterval\s+\S+;", f"writeInterval   {max_iter};", text, flags=re.M)
open(path, "w").write(text)
