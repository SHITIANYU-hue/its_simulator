def pretty_format_list(liste):
    if len(liste) < 10:
        str_decay = ''.join(["{:.2f} ".format(eps) for eps in liste])
    else:
        str_decay = "{} {} ... {} {}".format(liste[0], liste[1], liste[-2], liste[-1])
    return str_decay

def get_cls(module, class_name):
    import importlib
    module = importlib.import_module(module)
    return getattr(module, class_name)

